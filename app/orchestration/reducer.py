from __future__ import annotations

from dataclasses import dataclass

from app.orchestration.handoff import AgentStateDelta, Handoff
from app.orchestration.failures import (
    TaskOutcome,
    business_failure,
    failure_from_exception,
)
from app.orchestration.state import NodeStatus, TaskNode, TaskState


class StateReductionError(RuntimeError):
    pass


class StateVersionConflictError(StateReductionError):
    pass


class ArtifactContractError(StateReductionError):
    pass


@dataclass(frozen=True)
class ReductionResult:
    previous_task_status: str
    current_task_status: str
    node_status: str
    artifact_id: str


class StateReducer:
    """The sole compatibility boundary that merges Agent artifacts into shared state."""

    def apply_handoff(
        self,
        state: TaskState,
        node: TaskNode,
        handoff: Handoff,
        *,
        expected_state_version: int,
    ) -> ReductionResult:
        if state.state_version != expected_state_version:
            raise StateVersionConflictError(
                f"Expected state version {expected_state_version}, found {state.state_version}"
            )
        artifact = handoff.artifact
        if artifact is None:
            raise ArtifactContractError("Handoff must include a typed artifact")
        if artifact.task_id != state.task_id or handoff.task_id != state.task_id:
            raise ArtifactContractError("Artifact task_id does not match shared state")
        if artifact.producer != handoff.source_agent or node.agent_name != handoff.source_agent:
            raise ArtifactContractError("Artifact producer does not match source Agent")
        if artifact.input_state_version != expected_state_version:
            raise StateVersionConflictError(
                "Artifact was produced from a different state version"
            )
        if artifact.legacy_result() != handoff.result:
            raise ArtifactContractError("Artifact payload does not match Handoff result")

        previous_task_status = state.status
        state.handoffs.append(handoff)
        state.artifacts[artifact.artifact_id] = artifact
        state.latest_artifacts[handoff.source_agent] = artifact.artifact_id
        state.agent_outputs[handoff.source_agent] = artifact.legacy_result()
        self.merge_delta(state, handoff.source_agent, handoff.state_delta)

        if handoff.status == "completed":
            node.status = NodeStatus.completed
        elif handoff.status == "requires_input":
            node.status = NodeStatus.completed
            state.status = "waiting_for_input"
            state.outcome = TaskOutcome.waiting_for_input
        elif handoff.status == "requires_revision":
            node.status = NodeStatus.completed
        elif handoff.status == "requires_review":
            node.status = NodeStatus.skipped
            state.status = "waiting_for_approval"
            state.outcome = TaskOutcome.awaiting_approval
        else:
            node.status = NodeStatus.failed
            state.status = "failed"
            if handoff.failure is not None:
                state.record_failure(handoff.failure)
            elif handoff.source_agent == "review_agent":
                violations = list(handoff.result.get("violations", []))
                notes = list(handoff.result.get("review_notes", []))
                first_code = str(violations[0] if violations else "review_rejected")
                blocking_findings = [
                    item
                    for item in handoff.result.get("review_findings", [])
                    if item.get("blocking")
                ]
                matched_finding = next(
                    (
                        item
                        for item in blocking_findings
                        if first_code
                        in {
                            str(item.get("code")),
                            f"llm_review:{item.get('code')}",
                        }
                    ),
                    None,
                )
                message = (
                    str(matched_finding.get("message"))
                    if matched_finding
                    else notes[0]
                    if notes
                    else "当前方案没有通过业务规则审核。"
                )
                state.record_failure(
                    business_failure(
                        code=first_code,
                        stage=node.node_id,
                        user_message=message,
                        developer_message=handoff.error or ", ".join(violations) or message,
                    )
                )
            else:
                state.record_failure(
                    failure_from_exception(
                        RuntimeError(handoff.error or "Agent returned a failed Handoff"),
                        stage=node.node_id,
                        agent_name=handoff.source_agent,
                    )
                )
        state.mark_updated()
        return ReductionResult(
            previous_task_status=previous_task_status,
            current_task_status=state.status,
            node_status=node.status.value,
            artifact_id=artifact.artifact_id,
        )

    @staticmethod
    def commit_batch(state: TaskState, *, expected_state_version: int) -> int:
        if state.state_version != expected_state_version:
            raise StateVersionConflictError(
                f"Expected state version {expected_state_version}, found {state.state_version}"
            )
        state.state_version += 1
        state.mark_updated()
        return state.state_version

    @staticmethod
    def merge_delta(
        state: TaskState,
        agent_name: str,
        delta: AgentStateDelta,
    ) -> None:
        """Merge non-business evidence from either a successful or failed Agent run."""
        if delta.context_usage is not None:
            state.context_usage[agent_name] = delta.context_usage
        if delta.memory_refs:
            state.memory_refs[agent_name] = list(delta.memory_refs)

        known_model_calls = {record.get("call_id") for record in state.model_records}
        state.model_records.extend(
            record
            for record in delta.model_records
            if record.get("call_id") not in known_model_calls
        )
        known_fallbacks = {
            (record.get("agent_name"), record.get("purpose"), record.get("error"))
            for record in state.model_fallbacks
        }
        state.model_fallbacks.extend(
            record
            for record in delta.model_fallbacks
            if (
                record.get("agent_name"),
                record.get("purpose"),
                record.get("error"),
            )
            not in known_fallbacks
        )
        known_degradations = {
            (item.code, item.stage, item.developer_message)
            for item in state.degradations
        }
        state.degradations.extend(
            item
            for item in delta.degradations
            if (item.code, item.stage, item.developer_message)
            not in known_degradations
        )
