from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError, wait
from contextvars import copy_context
from time import perf_counter

from app.observability.recorder import TraceRecorder
from app.observability.schemas import TraceEventType
from app.memory.working_memory import WorkingMemory
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.handoff import Handoff
from app.orchestration.handoff import AgentStateDelta
from app.orchestration.failures import (
    TaskOutcome,
    business_failure,
    failure_from_exception,
)
from app.orchestration.loop_detection import RepeatCallDetector
from app.orchestration.artifacts import artifact_from_result
from app.orchestration.a2a import A2ACoordinator, A2AContractError
from app.orchestration.reducer import StateReducer
from app.orchestration.snapshot import StateSnapshot
from app.orchestration.state import (
    NodeStatus,
    TaskNode,
    TaskState,
    WorkflowLoopState,
)
from app.tools.registry import ToolRegistry
from app.config import CAPABILITY_MAX_USES
from app.security.capability_tokens import CapabilityAuthority, CapabilityGrant
from app.reliability.classifier import build_error_signature, classify_failure
from app.reliability.dead_letter import get_dead_letter_store
from app.reliability.models import DeadLetterRecord, ExecutionReceipt, FailureTaxonomy
from app.reliability.policy import retry_decision
from app.reliability.circuit_breaker import CircuitOpenError
from app.tools.schemas import UnknownWriteStateError
from app.safety.content_revision import is_repairable_finding


class NodeExecutionTimeout(TimeoutError):
    """A running thread cannot be safely retried after its deadline expires."""

    safe_to_retry = False


class WorkflowExecutor:
    def __init__(
        self,
        agents: dict[str, object],
        tools: ToolRegistry,
        trace: TraceRecorder,
        checkpoint_store: CheckpointStore | None = None,
        working_memory: WorkingMemory | None = None,
        max_steps: int = 12,
        node_timeout_seconds: float = 120,
        workflow_timeout_seconds: float = 120,
        a2a_coordinator: A2ACoordinator | None = None,
        capability_authority: CapabilityAuthority | None = None,
    ) -> None:
        self.agents = agents
        self.tools = tools
        self.trace = trace
        self.checkpoints = checkpoint_store or CheckpointStore()
        self.working_memory = working_memory or WorkingMemory()
        self.max_steps = max_steps
        self.node_timeout_seconds = node_timeout_seconds
        self.workflow_timeout_seconds = workflow_timeout_seconds
        self._run_deadline: float | None = None
        self.a2a = a2a_coordinator or A2ACoordinator(
            delegation_timeout_seconds=node_timeout_seconds
        )
        self.capability_authority = capability_authority
        self._capability_grants: dict[str, CapabilityGrant] = {}
        self.repeat_detector = RepeatCallDetector()
        self.reducer = StateReducer()

    def run(self, state: TaskState) -> TaskState:
        self._run_deadline = perf_counter() + self.workflow_timeout_seconds
        initial_tool_call_ids = {
            record.call_id for record in self.tools.records(state.task_id)
        }
        self.tools.bind_retry_budget(state.task_id, state.retry_budget)
        if state.resume_count:
            self.tools.seed_recovery_results(state.task_id, state.tool_records)
        previous_status = state.status
        state.status = "running"
        state.outcome = TaskOutcome.running
        state.failure = None
        self._record_state_transition(state, "task", previous_status, state.status)
        self.working_memory.snapshot(state)
        checkpoint_version = self.checkpoints.save(state)
        self.trace.record_event(
            state.task_id,
            TraceEventType.checkpoint_saved,
            "storage",
            "checkpoint_store",
            "initial_checkpoint",
            status="completed",
            details={"task_status": state.status, "checkpoint_version": checkpoint_version},
        )
        steps = 0

        while steps < self.max_steps:
            if self._remaining_timeout() <= 0:
                previous_status = state.status
                state.status = "failed"
                state.record_failure(
                    failure_from_exception(
                        TimeoutError(
                            f"Workflow exceeded {self.workflow_timeout_seconds:g} seconds"
                        ),
                        stage="workflow",
                        agent_name="supervisor",
                        trace_refs=(state.run_id,),
                    )
                )
                self._record_state_transition(
                    state, "task", previous_status, state.status
                )
                break
            ready_nodes = self._ready_nodes(state)
            if not ready_nodes:
                break
            if self.repeat_detector.seen_too_often(
                "ready_nodes", self._ready_node_repeat_payload(state, ready_nodes)
            ):
                previous_status = state.status
                state.status = "failed"
                state.record_failure(
                    failure_from_exception(
                        RuntimeError("repeated_ready_node_set"),
                        stage="workflow",
                        agent_name="supervisor",
                    )
                )
                self._record_state_transition(state, "task", previous_status, state.status)
                self.trace.record_event(
                    state.task_id,
                    TraceEventType.error,
                    "orchestrator",
                    "loop_detector",
                    "loop_detection",
                    status="failed",
                    error={"type": "LoopDetected", "message": "repeated_ready_node_set"},
                )
                break

            self._run_ready_nodes(state, ready_nodes)
            self._sync_reliability_state(state)
            self.trace.record_event(
                state.task_id,
                TraceEventType.working_memory,
                "memory",
                "working_memory",
                "snapshot",
                status="completed",
                details={"snapshot": self.working_memory.snapshot(state)},
            )
            checkpoint_version = self.checkpoints.save(state)
            self.trace.record_event(
                state.task_id,
                TraceEventType.checkpoint_saved,
                "storage",
                "checkpoint_store",
                "step_checkpoint",
                status="completed",
                details={
                    "completed_steps": steps + len(ready_nodes),
                    "checkpoint_version": checkpoint_version,
                },
            )
            steps += len(ready_nodes)

            if state.status in {
                "failed", "needs_attention", "waiting_for_approval", "waiting_for_input"
            }:
                break

        if state.status == "running":
            previous_status = state.status
            if steps >= self.max_steps:
                state.status = "failed"
            else:
                state.status = "completed" if self._all_required_complete(state) else "failed"
            if state.status == "completed":
                state.outcome = TaskOutcome.completed
                state.failure = None
            elif state.failure is None:
                state.record_failure(
                    failure_from_exception(
                        RuntimeError(
                            "Workflow ended before all required nodes completed"
                            if steps < self.max_steps
                            else "Workflow step budget exhausted"
                        ),
                        stage="workflow",
                        agent_name="supervisor",
                    )
                )
            self._record_state_transition(state, "task", previous_status, state.status)
        state.mark_updated()
        self._sync_reliability_state(state)
        task_tool_records = self.tools.records(state.task_id)
        current_tool_records = [
            record.model_dump(mode="json")
            for record in task_tool_records
            if record.call_id not in initial_tool_call_ids
        ]
        checkpoint_version = self.checkpoints.save(state)
        self.trace.record_event(
            state.task_id,
            TraceEventType.run_completed,
            "orchestrator",
            "workflow_executor",
            "run",
            status=state.status,
            details={
                "status": state.status,
                "outcome": state.outcome.value,
                "failure": state.failure.model_dump(mode="json") if state.failure else None,
                "steps": steps,
                "tool_call_count": len(current_tool_records),
                "cumulative_tool_call_count": len(state.tool_records),
                "model_call_count": len(state.model_records),
                "checkpoint_version": checkpoint_version,
                "resume_count": state.resume_count,
            },
        )
        return state

    def _sync_reliability_state(self, state: TaskState) -> None:
        current = [
            record.model_dump(mode="json")
            for record in self.tools.records(state.task_id)
        ]
        known_call_ids = {record.get("call_id") for record in state.tool_records}
        state.tool_records.extend(
            record for record in current if record.get("call_id") not in known_call_ids
        )
        state.retry_budget = self.tools.retry_budget(state.task_id)
        for record in state.tool_records:
            input_hash = str(record.get("input_hash") or "")
            if not input_hash:
                continue
            state.execution_receipts[input_hash] = ExecutionReceipt(
                tool_name=str(record.get("tool_name") or "unknown"),
                input_hash=input_hash,
                output_hash=record.get("output_hash"),
                status=record.get("status", "failed"),
                side_effect=bool(record.get("side_effect")),
                reusable=(
                    record.get("status") == "completed"
                    and not bool(record.get("side_effect"))
                ),
                result=record.get("result_summary"),
            )

    def _run_ready_nodes(self, state: TaskState, nodes: list[TaskNode]) -> None:
        snapshot = StateSnapshot.capture(state)
        expected_state_version = snapshot.state_version
        try:
            self.a2a.assert_batch_budget(state, nodes)
        except Exception as exc:
            for node in nodes:
                previous_status = node.status.value
                node.status = NodeStatus.running
                self._record_state_transition(
                    state, f"node:{node.node_id}", previous_status, node.status.value
                )
                self._handle_node_error(state, node, exc)
            return

        prepared_nodes: list[TaskNode] = []
        preparation_error: Exception | None = None
        for node in nodes:
            previous_status = node.status.value
            node.status = NodeStatus.running
            self.trace.record_event(
                state.task_id,
                TraceEventType.node_started,
                "agent",
                node.agent_name,
                node.node_id,
                status="running",
                details={"dependencies": node.dependencies, "retry_count": node.retry_count},
            )
            self._record_state_transition(
                state, f"node:{node.node_id}", previous_status, node.status.value
            )
            try:
                delegation = self.a2a.create_for_node(state, node)
                self.a2a.transition(
                    state,
                    delegation.request.delegation_id,
                    "accepted",
                    actor=node.agent_name,
                )
                self.a2a.transition(
                    state,
                    delegation.request.delegation_id,
                    "running",
                    actor=node.agent_name,
                )
                self._issue_capability_grant(delegation)
                prepared_nodes.append(node)
            except Exception as exc:
                preparation_error = exc
                self._handle_node_error(state, node, exc)
                break

        if preparation_error is not None:
            for node in prepared_nodes:
                self._handle_node_error(
                    state,
                    node,
                    A2AContractError(
                        f"A2A batch cancelled after preparation failure: {preparation_error}"
                    ),
                )
            for node in nodes:
                if node not in prepared_nodes and node.status is NodeStatus.pending:
                    node.status = NodeStatus.failed
            return

        nodes = prepared_nodes
        if not nodes:
            return

        if len(nodes) == 1:
            self._run_one(state, nodes[0], expected_state_version)
            self.reducer.commit_batch(
                state, expected_state_version=expected_state_version
            )
            return

        pool = ThreadPoolExecutor(max_workers=len(nodes))
        future_to_node = {
            pool.submit(
                copy_context().run,
                self._invoke_agent,
                node,
                state,
                expected_state_version,
            ): node
            for node in nodes
        }
        done, not_done = wait(future_to_node, timeout=self._effective_node_timeout())
        for future in done:
            node = future_to_node[future]
            try:
                handoff = future.result()
            except Exception as exc:
                self._handle_node_error(state, node, exc)
                continue
            self._handle_handoff(
                state,
                node,
                handoff,
                expected_state_version=expected_state_version,
            )
        for future in not_done:
            node = future_to_node[future]
            future.cancel()
            self._handle_node_timeout(state, node)
        pool.shutdown(wait=not not_done, cancel_futures=bool(not_done))
        self.reducer.commit_batch(state, expected_state_version=expected_state_version)

    def _run_one(
        self,
        state: TaskState,
        node: TaskNode,
        expected_state_version: int | None = None,
    ) -> None:
        compatibility_call = expected_state_version is None
        effective_state_version = (
            state.state_version
            if expected_state_version is None
            else expected_state_version
        )
        start = perf_counter()
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(
            copy_context().run,
            self._invoke_agent,
            node,
            state,
            effective_state_version,
            not compatibility_call,
        )
        try:
            handoff = future.result(timeout=self._effective_node_timeout())
        except TimeoutError:
            if future.done():
                pool.shutdown(wait=True, cancel_futures=True)
                try:
                    handoff = future.result()
                except Exception as exc:
                    self._handle_node_error(state, node, exc)
                else:
                    elapsed_ms = round((perf_counter() - start) * 1000, 2)
                    if compatibility_call:
                        self._handle_legacy_handoff(state, node, handoff)
                    else:
                        self._handle_handoff(
                            state,
                            node,
                            handoff,
                            elapsed_ms=elapsed_ms,
                            expected_state_version=effective_state_version,
                        )
                return
            future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            self._handle_node_timeout(state, node)
            return
        except Exception as exc:
            pool.shutdown(wait=True, cancel_futures=True)
            self._handle_node_error(state, node, exc)
            return
        pool.shutdown(wait=True)
        elapsed_ms = round((perf_counter() - start) * 1000, 2)
        if compatibility_call:
            self._handle_legacy_handoff(state, node, handoff)
        else:
            self._handle_handoff(
                state,
                node,
                handoff,
                elapsed_ms=elapsed_ms,
                expected_state_version=effective_state_version,
            )

    @staticmethod
    def _handle_legacy_handoff(
        state: TaskState, node: TaskNode, handoff: Handoff
    ) -> None:
        """Preserve the V14 private test hook; production batches require artifacts."""
        state.handoffs.append(handoff)
        state.agent_outputs[handoff.source_agent] = handoff.result
        if handoff.status == "completed":
            node.status = NodeStatus.completed
        elif handoff.status == "requires_revision":
            node.status = NodeStatus.completed
        elif handoff.status == "requires_review":
            node.status = NodeStatus.skipped
            state.status = "waiting_for_approval"
        else:
            node.status = NodeStatus.failed
            state.status = "failed"
        state.mark_updated()

    def _handle_node_timeout(self, state: TaskState, node: TaskNode) -> None:
        error = NodeExecutionTimeout(
            f"Node '{node.node_id}' exceeded {self.node_timeout_seconds:g} seconds; "
            "the in-flight invocation will not be retried"
        )
        self._handle_node_error(state, node, error, error_type="timeout")

    def _remaining_timeout(self) -> float:
        if self._run_deadline is None:
            return self.node_timeout_seconds
        return max(0.0, self._run_deadline - perf_counter())

    def _effective_node_timeout(self) -> float:
        if self._run_deadline is None:
            return self.node_timeout_seconds
        return max(0.001, min(self.node_timeout_seconds, self._remaining_timeout()))

    def _handle_handoff(
        self,
        state: TaskState,
        node: TaskNode,
        handoff: Handoff,
        elapsed_ms: float | None = None,
        *,
        expected_state_version: int,
    ) -> None:
        if node.delegation_id:
            delegation = state.a2a_delegations[node.delegation_id]
            if handoff.delegation_id != node.delegation_id:
                raise A2AContractError(
                    "Handoff delegation_id does not match active A2A request"
                )
            if handoff.input_artifact_refs != delegation.request.input_artifact_refs:
                raise A2AContractError(
                    "Handoff input Artifact references do not match A2A request"
                )
        if node.delegation_id and handoff.artifact is not None:
            self.a2a.validate_completion(state, node, handoff.artifact)
        reduction = self.reducer.apply_handoff(
            state,
            node,
            handoff,
            expected_state_version=expected_state_version,
        )
        previous_task_status = reduction.previous_task_status
        if node.delegation_id and handoff.artifact is not None:
            if handoff.status in {
                "completed", "requires_review", "requires_revision", "requires_input"
            }:
                self.a2a.complete(state, node, handoff.artifact)
            else:
                self.a2a.fail_active(
                    state, node, handoff.error or "Agent returned failed Handoff"
                )
        self._revoke_capability_grant(node, f"delegation_{handoff.status}")
        self.trace.record_event(
            state.task_id,
            TraceEventType.agent_completed,
            "agent",
            node.agent_name,
            node.node_id,
            status=handoff.status,
            duration_ms=elapsed_ms,
            details={
                "status": handoff.status,
                "confidence": handoff.confidence,
                "handoff_target": handoff.target_agent,
                "context_usage": state.context_usage.get(node.agent_name, {}),
                "memory_refs": state.memory_refs.get(node.agent_name, []),
                "model_records": [
                    record for record in state.model_records if record.get("agent_name") == node.agent_name
                ],
                "artifact": {
                    "artifact_id": reduction.artifact_id,
                    "artifact_type": handoff.artifact.artifact_type,
                    "schema_version": handoff.artifact.schema_version,
                    "content_hash": handoff.artifact.content_hash,
                    "input_state_version": handoff.artifact.input_state_version,
                    "promotion_protocol_version": getattr(
                        handoff.artifact, "promotion_protocol_version", None
                    ),
                    "core_protocol_version": getattr(
                        handoff.artifact, "core_protocol_version", None
                    ),
                },
                "error": handoff.error,
                "a2a": {
                    "delegation_id": node.delegation_id,
                    "status": (
                        state.a2a_delegations[node.delegation_id].status
                        if node.delegation_id
                        else None
                    ),
                    "input_artifact_refs": (
                        list(
                            state.a2a_delegations[
                                node.delegation_id
                            ].request.input_artifact_refs
                        )
                        if node.delegation_id
                        else []
                    ),
                    "output_artifact_ref": reduction.artifact_id,
                },
            },
            error={"type": "AgentHandoffError", "message": handoff.error}
            if handoff.error
            else None,
        )
        corrections = [
            *handoff.result.get("semantic_corrections", []),
            *handoff.result.get("correction_audit", []),
        ]
        seen_corrections: set[str] = set()
        for correction in corrections:
            correction_id = str(correction.get("correction_id") or "")
            if correction_id and correction_id in seen_corrections:
                continue
            if correction_id:
                seen_corrections.add(correction_id)
            self.trace.record_event(
                state.task_id,
                TraceEventType.semantic_correction,
                "guardrail",
                node.agent_name,
                str(correction.get("field_path") or node.node_id),
                status=str(correction.get("status") or "corrected"),
                details=correction,
            )
        self._record_state_transition(state, f"node:{node.node_id}", "running", node.status.value)
        if previous_task_status != state.status:
            self._record_state_transition(state, "task", previous_task_status, state.status)
        if state.status == "waiting_for_approval":
            self.trace.record_event(
                state.task_id,
                TraceEventType.approval_waiting,
                "guardrail",
                "human_approval",
                node.node_id,
                status=state.status,
                details={"agent_name": node.agent_name, "reason": handoff.error},
            )
        if state.status == "waiting_for_input":
            self.trace.record_event(
                state.task_id,
                TraceEventType.state_transition,
                "guardrail",
                "market_price_gate",
                node.node_id,
                status=state.status,
                details={
                    "agent_name": node.agent_name,
                    "reason": handoff.error,
                    "assessment": handoff.result,
                },
            )
        if handoff.status == "requires_revision":
            self._schedule_compliance_revision(
                state, handoff, reduction.artifact_id
            )
        elif (
            node.agent_name in {"listing_agent", "strategy_agent"}
            and handoff.status == "completed"
        ):
            self._schedule_revision_review(
                state, node.agent_name, reduction.artifact_id
            )
        elif node.node_id == "review":
            self._finish_revision_loop(state, handoff.status)

    def _schedule_compliance_revision(
        self, state: TaskState, handoff: Handoff, review_artifact_ref: str
    ) -> None:
        loop = state.workflow_loops.get("compliance_repair")
        if loop is None:
            loop = WorkflowLoopState()
            state.workflow_loops["compliance_repair"] = loop
        if loop.iteration >= loop.max_iterations:
            loop.phase = "exhausted"
            loop.stop_reason = "revision_budget_exhausted"
            state.nodes["review"].status = NodeStatus.failed
            state.status = "failed"
            state.record_failure(
                business_failure(
                    code="revision_budget_exhausted",
                    stage="review",
                    user_message="自动修订已达到次数上限，方案仍未通过审核。",
                    developer_message="Compliance revision budget exhausted",
                )
            )
            return

        targets = list(
            dict.fromkeys(
                handoff.result.get("revision_targets")
                or (
                    [handoff.result.get("revision_target")]
                    if handoff.result.get("revision_target")
                    else []
                )
            )
        )
        targets = [
            target
            for target in targets
            if target in {"listing_agent", "strategy_agent"}
        ]
        if not targets:
            loop.phase = "exhausted"
            loop.stop_reason = "missing_revision_target"
            state.nodes["review"].status = NodeStatus.failed
            state.status = "failed"
            state.record_failure(
                failure_from_exception(
                    RuntimeError("missing_revision_target"),
                    stage="review",
                    agent_name="review_agent",
                )
            )
            return

        feedback = list(handoff.result.get("review_findings", []))
        fingerprint = self._finding_fingerprint(feedback)
        repeated_finding = bool(
            loop.iteration > 0
            and loop.finding_fingerprint
            and loop.finding_fingerprint == fingerprint
        )
        loop.iteration += 1
        loop.phase = "revision_pending"
        loop.feedback = feedback
        loop.source_artifact_ref = review_artifact_ref
        loop.target_agents = targets
        loop.completed_agents = []
        loop.revised_artifact_refs = {}
        loop.finding_fingerprint = fingerprint
        generated_content_repair = bool(feedback) and all(
            finding.get("claim_origin") == "agent_generated"
            and is_repairable_finding(finding)
            for finding in feedback
            if finding.get("blocking")
        )
        # Generated-content defects belong to the system. Apply exact field-level
        # repair on the first pass instead of asking the model to regenerate the
        # whole artifact and potentially introduce a new claim.
        loop.safe_finalize = repeated_finding or generated_content_repair
        loop.stop_reason = None
        node_by_agent = {
            "listing_agent": (state.nodes["listing"], "listing.revise"),
            "strategy_agent": (state.nodes["strategy"], "strategy.revise"),
        }
        state.status = "running"
        state.outcome = TaskOutcome.running
        state.failure = None
        for target in targets:
            target_node, capability_id = node_by_agent[target]
            previous_status = target_node.status.value
            target_node.status = NodeStatus.pending
            target_node.capability_id = capability_id
            target_node.supplemental_artifact_refs = [review_artifact_ref]
            self._record_state_transition(
                state,
                f"node:{target_node.node_id}",
                previous_status,
                target_node.status.value,
            )
        state.mark_updated()
        self.trace.record_event(
            state.task_id,
            TraceEventType.state_transition,
            "orchestrator",
            "compliance_repair_loop",
            "revision_scheduled",
            status="revision_pending",
            details={
                "iteration": loop.iteration,
                "max_iterations": loop.max_iterations,
                "review_artifact_ref": review_artifact_ref,
                "finding_codes": [
                    finding.get("code") for finding in loop.feedback
                ],
                "target_agents": targets,
                "safe_finalize": loop.safe_finalize,
                "finding_fingerprint": fingerprint,
            },
        )

    def _schedule_revision_review(
        self, state: TaskState, agent_name: str, artifact_ref: str
    ) -> None:
        loop = state.workflow_loops.get("compliance_repair") or state.workflow_loops.get(
            "listing_review"
        )
        if (
            loop is None
            or loop.phase != "revision_pending"
            or agent_name not in loop.target_agents
        ):
            return
        if agent_name not in loop.completed_agents:
            loop.completed_agents.append(agent_name)
        loop.revised_artifact_refs[agent_name] = artifact_ref
        if loop.revised_artifact_ref is None:
            loop.revised_artifact_ref = artifact_ref
        if set(loop.completed_agents) != set(loop.target_agents):
            return
        loop.phase = "review_pending"
        review = state.nodes["review"]
        previous_review_status = review.status.value
        review.status = NodeStatus.pending
        state.status = "running"
        state.outcome = TaskOutcome.running
        state.failure = None
        self._record_state_transition(
            state,
            "node:review",
            previous_review_status,
            review.status.value,
        )
        state.mark_updated()
        self.trace.record_event(
            state.task_id,
            TraceEventType.state_transition,
            "orchestrator",
            "compliance_repair_loop",
            "recheck_scheduled",
            status="review_pending",
            details={
                "iteration": loop.iteration,
                "revised_artifact_refs": loop.revised_artifact_refs,
                "safe_finalize": loop.safe_finalize,
            },
        )

    @staticmethod
    def _finish_revision_loop(state: TaskState, handoff_status: str) -> None:
        loop = state.workflow_loops.get("compliance_repair") or state.workflow_loops.get(
            "listing_review"
        )
        if loop is None or loop.phase != "review_pending":
            return
        loop.phase = "completed" if handoff_status == "completed" else "exhausted"
        if handoff_status != "completed":
            loop.stop_reason = "revision_budget_exhausted_or_still_blocked"

    @staticmethod
    def _finding_fingerprint(findings: list[dict]) -> str:
        normalized = [
            {
                key: finding.get(key)
                for key in (
                    "code",
                    "source_agent",
                    "field_path",
                    "claim_text",
                )
            }
            for finding in findings
            if finding.get("blocking")
        ]
        payload = json.dumps(
            sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _handle_node_error(
        self, state: TaskState, node: TaskNode, exc: Exception, error_type: str = "exception"
    ) -> None:
        failure_delta = getattr(exc, "agent_state_delta", None)
        if isinstance(failure_delta, AgentStateDelta):
            self.reducer.merge_delta(state, node.agent_name, failure_delta)
        self._sync_reliability_state(state)
        self.a2a.fail_active(state, node, exc)
        self._revoke_capability_grant(node, f"delegation_error:{type(exc).__name__}")
        previous_task_status = state.status
        node.retry_count += 1
        taxonomy = classify_failure(exc)
        signature = build_error_signature(
            exc, agent_name=node.agent_name, code=type(exc).__name__
        )
        budget = self.tools.retry_budget(state.task_id)
        decision = retry_decision(
            component=f"agent:{node.agent_name}",
            category=taxonomy,
            signature=signature,
            attempt=node.retry_count,
            budget_remaining=budget.remaining,
        )
        # Provider and tool adapters already own their bounded retries. A whole-node
        # retry is opt-in so nested retry layers cannot multiply runtime silently.
        adapter_retry_safe = bool(getattr(exc, "safe_to_retry", False))
        retry_allowed = (
            adapter_retry_safe
            and decision.allowed
            and node.retry_count <= node.max_retries
        )
        if retry_allowed:
            decision = decision.model_copy(update={"allowed": True})
            budget.consume(decision)
            self.tools.bind_retry_budget(state.task_id, budget)
            node.status = NodeStatus.pending
            status = "retry_scheduled"
        else:
            node.status = NodeStatus.failed
            failure = failure_from_exception(
                exc,
                stage=node.node_id,
                agent_name=node.agent_name,
                trace_refs=(state.run_id,),
            )
            if failure.code == "model_network_unavailable":
                transport_attempts = sum(
                    max(1, int(record.get("request_attempts", 1) or 1))
                    for record in state.model_records
                    if record.get("agent_name") == node.agent_name
                    and record.get("status") == "failed"
                )
                transport_attempts = max(
                    transport_attempts, failure.transport_attempts
                )
                failure = failure.model_copy(
                    update={
                        "transport_attempts": transport_attempts,
                        "user_message": (
                            "模型服务网络连接暂时中断，"
                            f"系统已自动尝试 {transport_attempts} 次，仍未获得完整响应。"
                            "本次没有修改店铺，已保留完成的上游结果，"
                            "可以稍后重试当前环节。"
                        ),
                    }
                )
            failure = failure.model_copy(
                update={
                    "taxonomy_category": taxonomy,
                    "error_signature": signature,
                    "retryable": decision.allowed,
                    "retry_attempt": node.retry_count,
                    "retry_budget_remaining": budget.remaining,
                }
            )
            needs_attention = (
                isinstance(exc, (UnknownWriteStateError, CircuitOpenError))
                or decision.reason == "task_retry_budget_exhausted"
                or taxonomy is FailureTaxonomy.unknown
            )
            state.status = "needs_attention" if needs_attention else "failed"
            state.record_failure(failure)
            if needs_attention:
                state.needs_attention = True
                state.outcome = TaskOutcome.needs_attention
                get_dead_letter_store().enqueue(
                    DeadLetterRecord(
                        task_id=state.task_id,
                        run_id=state.run_id,
                        tenant_id=state.principal.tenant_id,
                        stage=node.node_id,
                        agent_name=node.agent_name,
                        tool_name=(
                            state.tool_records[-1].get("tool_name")
                            if state.tool_records
                            else None
                        ),
                        category=taxonomy,
                        error_signature=signature,
                        user_message=failure.user_message,
                        developer_message=failure.developer_message,
                        checkpoint_version=state.checkpoint_version,
                        payload={"retry_decision": decision.model_dump(mode="json")},
                    )
                )
            status = "needs_attention" if needs_attention else "failed"
        state.reliability_events.append(
            {
                "event": "retry_decision",
                "node": node.node_id,
                **decision.model_dump(mode="json"),
                "adapter_retry_safe": adapter_retry_safe,
            }
        )
        self.trace.record_event(
            state.task_id,
            TraceEventType.error,
            "agent",
            node.agent_name,
            node.node_id,
            status=status,
            details={
                "retry_count": node.retry_count,
                "max_retries": node.max_retries,
                "retry_allowed": retry_allowed,
                "failure_taxonomy": taxonomy.value,
                "error_signature": signature,
                "task_retry_budget_remaining": budget.remaining,
            },
            error={"type": error_type, "message": str(exc)},
        )
        self.trace.record_event(
            state.task_id,
            TraceEventType.agent_completed,
            "agent",
            node.agent_name,
            node.node_id,
            status=status,
            details={"retry_count": node.retry_count, "retry_allowed": retry_allowed},
            error={"type": error_type, "message": str(exc)},
        )
        self._record_state_transition(state, f"node:{node.node_id}", "running", node.status.value)
        if previous_task_status != state.status:
            self._record_state_transition(state, "task", previous_task_status, state.status)
        state.mark_updated()

    def _invoke_agent(
        self,
        node: TaskNode,
        state: TaskState,
        input_state_version: int,
        require_typed_artifact: bool = True,
    ) -> Handoff:
        agent = self.agents[node.agent_name]
        local_state = state.model_copy(deep=True)
        existing_model_records = len(local_state.model_records)
        existing_fallbacks = len(local_state.model_fallbacks)
        try:
            with self.tools.agent_scope(
                node.agent_name,
                approved=local_state.approved,
                approved_by=local_state.approved_by,
                task_id=local_state.task_id,
                tenant_id=local_state.principal.tenant_id,
                delegation_id=node.delegation_id,
                capability_id=node.capability_id,
                capability_token=(
                    self._capability_grants[node.delegation_id].token
                    if node.delegation_id in self._capability_grants
                    else None
                ),
                capability_token_id=(
                    self._capability_grants[node.delegation_id].claims.token_id
                    if node.delegation_id in self._capability_grants
                    else None
                ),
            ), agent.model_adapter.agent_scope(node.agent_name):
                handoff = agent.run(local_state)
        except Exception as exc:
            setattr(
                exc,
                "agent_state_delta",
                self._build_agent_delta(
                    local_state,
                    node.agent_name,
                    existing_model_records,
                    existing_fallbacks,
                ),
            )
            raise

        if not require_typed_artifact:
            return handoff

        delegation_id = local_state.nodes[node.node_id].delegation_id
        input_artifact_refs: tuple[str, ...] = ()
        if delegation_id:
            input_artifact_refs = local_state.a2a_delegations[
                delegation_id
            ].request.input_artifact_refs
        evidence_refs = tuple(
            dict.fromkeys([*handoff.evidence_refs, *input_artifact_refs])
        )
        handoff = handoff.model_copy(
            update={
                "delegation_id": delegation_id,
                "input_artifact_refs": input_artifact_refs,
                "evidence_refs": list(evidence_refs),
            }
        )

        artifact = artifact_from_result(
            task_id=local_state.task_id,
            producer=handoff.source_agent,
            result=handoff.result,
            input_state_version=input_state_version,
            confidence=handoff.confidence,
            evidence_refs=evidence_refs,
        )
        delta = self._build_agent_delta(
            local_state,
            node.agent_name,
            existing_model_records,
            existing_fallbacks,
        )
        return handoff.model_copy(
            update={
                "artifact": artifact,
                "result": artifact.legacy_result(),
                "state_delta": delta,
            }
        )

    @staticmethod
    def _build_agent_delta(
        local_state: TaskState,
        agent_name: str,
        existing_model_records: int,
        existing_fallbacks: int,
    ) -> AgentStateDelta:
        return AgentStateDelta(
            context_usage=local_state.context_usage.get(agent_name),
            memory_refs=tuple(local_state.memory_refs.get(agent_name, [])),
            model_records=tuple(local_state.model_records[existing_model_records:]),
            model_fallbacks=tuple(local_state.model_fallbacks[existing_fallbacks:]),
            degradations=tuple(local_state.degradations),
        )

    def _issue_capability_grant(self, delegation) -> None:
        if self.capability_authority is None:
            return
        _, capability = self.a2a.directory.discover(
            delegation.request.capability_id,
            expected_agent=delegation.request.receiver_agent,
        )
        grant = self.capability_authority.issue(
            delegation.request,
            allowed_tools=capability.allowed_tools,
            max_uses=CAPABILITY_MAX_USES,
        )
        self._capability_grants[delegation.request.delegation_id] = grant

    def _revoke_capability_grant(self, node: TaskNode, reason: str) -> None:
        if self.capability_authority is None or not node.delegation_id:
            return
        grant = self._capability_grants.get(node.delegation_id)
        if grant is not None:
            self.capability_authority.revoke(grant.claims.token_id, reason=reason)

    def _record_state_transition(
        self, state: TaskState, subject: str, previous: str, current: str
    ) -> None:
        self.trace.record_event(
            state.task_id,
            TraceEventType.state_transition,
            "state",
            subject,
            "transition",
            status=current,
            details={"from": previous, "to": current},
        )

    def _ready_nodes(self, state: TaskState) -> list[TaskNode]:
        ready: list[TaskNode] = []
        listing_review_loop = state.workflow_loops.get(
            "compliance_repair"
        ) or state.workflow_loops.get("listing_review")
        revision_gate_active = bool(
            listing_review_loop
            and listing_review_loop.phase
            in {"revision_pending", "review_pending"}
        )
        for node in state.nodes.values():
            if node.status is not NodeStatus.pending:
                continue
            if node.node_id == "browser" and revision_gate_active:
                continue
            if all(state.nodes[dep].status is NodeStatus.completed for dep in node.dependencies):
                ready.append(node)
        return ready

    @staticmethod
    def _ready_node_repeat_payload(
        state: TaskState, ready_nodes: list[TaskNode]
    ) -> dict[str, object]:
        """Include bounded repair progress so a valid re-review is not a loop."""

        payload: dict[str, object] = {
            "nodes": [node.node_id for node in ready_nodes]
        }
        loop = state.workflow_loops.get("compliance_repair") or state.workflow_loops.get(
            "listing_review"
        )
        if loop and loop.phase in {"revision_pending", "review_pending"}:
            payload["repair"] = {
                "iteration": loop.iteration,
                "phase": loop.phase,
                "finding_fingerprint": loop.finding_fingerprint,
                "target_agents": loop.target_agents,
                "completed_agents": loop.completed_agents,
                "revised_artifact_refs": loop.revised_artifact_refs,
            }
        return payload

    def _all_required_complete(self, state: TaskState) -> bool:
        if state.constraints.get("pricing_confirmation_action") == "market_analysis_only":
            return all(
                node.status in {NodeStatus.completed, NodeStatus.skipped}
                for node in state.nodes.values()
            )
        return all(node.status is NodeStatus.completed for node in state.nodes.values())
