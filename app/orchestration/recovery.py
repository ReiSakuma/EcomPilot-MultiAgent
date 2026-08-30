from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.orchestration.state import NodeStatus, RecoveryRecord, TaskState
from app.orchestration.failures import TaskOutcome
from app.safety.approval import Approval


class RecoveryError(RuntimeError):
    pass


class RecoveryNotAllowedError(RecoveryError):
    pass


class RecoveryConflictError(RecoveryError):
    pass


class RecoveryValidationError(RecoveryError):
    pass


class ConstraintPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost: float | None = Field(default=None, ge=0)
    target_price: float | None = Field(default=None, gt=0)
    inventory: int | None = Field(default=None, ge=0)
    planned_units: int | None = Field(default=None, ge=0)
    min_margin_rate: float | None = Field(default=None, ge=0, lt=1)
    target_audience: str | None = Field(default=None, min_length=1, max_length=80)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    force_bad_title: bool | None = None
    force_execution_verification_failure: bool | None = None
    pricing_profile: str | None = Field(default=None, pattern="^(commodity|standard|differentiated)$")
    pricing_confirmation_action: str | None = Field(
        default=None,
        pattern="^(adopt_suggested_price|keep_original_with_evidence|market_analysis_only)$",
    )
    pricing_override: bool | None = None
    pricing_override_evidence: list[str] | None = Field(default=None, min_length=1, max_length=8)
    price_confirmation_request_id: str | None = Field(default=None, min_length=3, max_length=100)


class RecoveryCoordinator:
    _lock = RLock()
    _active_tasks: set[str] = set()

    @classmethod
    @contextmanager
    def claim(cls, task_id: str):
        with cls._lock:
            if task_id in cls._active_tasks:
                raise RecoveryConflictError(f"Task '{task_id}' is already being resumed")
            cls._active_tasks.add(task_id)
        try:
            yield
        finally:
            with cls._lock:
                cls._active_tasks.discard(task_id)


class RecoveryManager:
    def prepare(
        self,
        state: TaskState,
        new_run_id: str,
        *,
        approval: Approval | None = None,
        retry_node: str | None = None,
        constraint_updates: dict[str, Any] | None = None,
        requested_by: str | None = None,
        reason: str | None = None,
    ) -> tuple[TaskState, RecoveryRecord]:
        source_run_id = state.run_id
        source_version = state.checkpoint_version
        updates = self._validate_updates(constraint_updates or {})

        if state.status == "waiting_for_approval":
            if approval is None or not approval.approved:
                raise RecoveryNotAllowedError("Explicit approval is required to resume this task")
            restarted_nodes = self._prepare_approval_resume(state)
            state.approved = True
            state.approved_by = approval.approver or requested_by
            state.approval_reason = approval.reason or reason
            action = "approval_resume"
        elif state.status == "waiting_for_input":
            action_name = str(updates.get("pricing_confirmation_action") or "")
            if not action_name:
                price_fields = {
                    "cost", "target_price", "min_margin_rate", "pricing_profile"
                }
                if not price_fields.intersection(updates):
                    raise RecoveryValidationError(
                        "pricing_confirmation_action or a revised price constraint "
                        "is required to resume price confirmation"
                    )
                updates["pricing_override"] = False
                updates.pop("pricing_override_evidence", None)
                restarted_nodes = self._restart_node_and_descendants(
                    state, "market_price_gate"
                )
                action = "market_price_constraint_replan"
            elif action_name == "adopt_suggested_price":
                if "target_price" not in updates:
                    raise RecoveryValidationError(
                        "adopt_suggested_price requires target_price"
                    )
                updates["pricing_override"] = False
                updates.pop("pricing_override_evidence", None)
                restarted_nodes = self._restart_node_and_descendants(
                    state, "market_price_gate"
                )
                action = "market_price_adopted"
            elif action_name == "keep_original_with_evidence":
                if not updates.get("pricing_override_evidence"):
                    raise RecoveryValidationError(
                        "keep_original_with_evidence requires pricing_override_evidence"
                    )
                updates["pricing_override"] = True
                restarted_nodes = self._restart_node_and_descendants(
                    state, "market_price_gate"
                )
                action = "market_price_override"
            elif action_name == "market_analysis_only":
                restarted_nodes = []
                for node_id in ("listing", "strategy", "review", "browser"):
                    if node_id in state.nodes:
                        state.nodes[node_id].status = NodeStatus.skipped
                action = "market_analysis_only"
            else:
                raise RecoveryValidationError("Unsupported pricing confirmation action")
        elif state.status in {"failed", "needs_attention"}:
            unknown_writes = [
                record
                for record in state.tool_records
                if record.get("status") == "unknown" and record.get("side_effect")
            ]
            if unknown_writes:
                raise RecoveryNotAllowedError(
                    "Unknown write state must be reconciled by readback before recovery"
                )
            target = self._resolve_retry_node(
                state, retry_node, allow_completed_restart=bool(updates)
            )
            target_was_failed = state.nodes[target].status is NodeStatus.failed
            restarted_nodes = self._restart_node_and_descendants(state, target)
            if approval is not None and approval.approved:
                state.approved = True
                state.approved_by = approval.approver or requested_by
                state.approval_reason = approval.reason or reason
            action = "failed_node_retry" if target_was_failed else "restart_from_node"
        else:
            raise RecoveryNotAllowedError(
                f"Task status '{state.status}' cannot be resumed"
            )

        # Apply the validated and policy-derived patch atomically. In particular,
        # pricing_override is derived above and must not be lost between recovery
        # validation and the restarted market-price gate.
        state.constraints.update(updates)
        state.parent_run_id = source_run_id
        state.run_id = new_run_id
        state.resume_count += 1
        state.status = "recovering"
        state.outcome = TaskOutcome.running
        state.failure = None
        state.mark_updated()
        record = RecoveryRecord(
            from_run_id=source_run_id,
            to_run_id=new_run_id,
            action=action,
            restarted_nodes=restarted_nodes,
            checkpoint_version=source_version,
            requested_by=requested_by or state.approved_by,
            reason=reason or state.approval_reason,
            created_at=datetime.now(timezone.utc),
        )
        state.recovery_history.append(record)
        return state, record

    @staticmethod
    def reconcile_unknown_writes(
        state: TaskState, readback: dict[str, bool]
    ) -> list[str]:
        """Resolve timed-out writes from authoritative readback evidence."""

        resolved: list[str] = []
        for record in state.tool_records:
            if record.get("status") != "unknown" or not record.get("side_effect"):
                continue
            input_hash = str(record.get("input_hash") or "")
            if input_hash not in readback:
                raise RecoveryValidationError(
                    f"Missing readback decision for unknown write '{input_hash}'"
                )
            record["status"] = "completed" if readback[input_hash] else "failed"
            record["validation_status"] = "reconciled_by_readback"
            record["reconciliation_applied"] = bool(readback[input_hash])
            resolved.append(input_hash)
        if not resolved:
            raise RecoveryNotAllowedError("Task has no unknown write to reconcile")
        state.reliability_events.append(
            {
                "event": "unknown_write_reconciled",
                "input_hashes": resolved,
                "source": "authoritative_readback",
            }
        )
        state.mark_updated()
        return resolved

    @staticmethod
    def _validate_updates(updates: dict[str, Any]) -> dict[str, Any]:
        if not updates:
            return {}
        try:
            patch = ConstraintPatch.model_validate(updates)
        except ValidationError as exc:
            raise RecoveryValidationError(f"Invalid constraint updates: {exc}") from exc
        return patch.model_dump(exclude_unset=True, exclude_none=True)

    def _prepare_approval_resume(self, state: TaskState) -> list[str]:
        candidates = [
            node_id
            for node_id, node in state.nodes.items()
            if node.status is NodeStatus.skipped and node.agent_name == "browser_agent"
        ]
        if len(candidates) != 1:
            raise RecoveryNotAllowedError(
                "Approval resume requires exactly one skipped browser node"
            )
        return self._reset_nodes(state, candidates)

    @staticmethod
    def _resolve_retry_node(
        state: TaskState,
        retry_node: str | None,
        *,
        allow_completed_restart: bool,
    ) -> str:
        failed = [node_id for node_id, node in state.nodes.items() if node.status is NodeStatus.failed]
        target = retry_node or (failed[0] if len(failed) == 1 else None)
        if target is None:
            raise RecoveryValidationError("retry_node is required when failure is ambiguous")
        if target not in state.nodes:
            raise RecoveryValidationError(f"Unknown retry node: '{target}'")
        target_status = state.nodes[target].status
        if target_status is NodeStatus.failed:
            return target
        if target_status is not NodeStatus.completed or not allow_completed_restart:
            raise RecoveryNotAllowedError(
                f"Node '{target}' is not failed; restarting completed nodes requires constraint updates"
            )
        descendants = {target}
        changed = True
        while changed:
            changed = False
            for node_id, node in state.nodes.items():
                if node_id not in descendants and any(dep in descendants for dep in node.dependencies):
                    descendants.add(node_id)
                    changed = True
        if not any(state.nodes[node_id].status is NodeStatus.failed for node_id in descendants):
            raise RecoveryNotAllowedError(
                f"Node '{target}' is not upstream of the current failure"
            )
        return target

    def _restart_node_and_descendants(self, state: TaskState, target: str) -> list[str]:
        affected = {target}
        changed = True
        while changed:
            changed = False
            for node_id, node in state.nodes.items():
                if node_id not in affected and any(dep in affected for dep in node.dependencies):
                    affected.add(node_id)
                    changed = True
        ordered = [node_id for node_id in state.nodes if node_id in affected]
        return self._reset_nodes(state, ordered)

    @staticmethod
    def _reset_nodes(state: TaskState, node_ids: list[str]) -> list[str]:
        affected_agents = {state.nodes[node_id].agent_name for node_id in node_ids}
        for node_id in node_ids:
            node = state.nodes[node_id]
            node.status = NodeStatus.pending
            node.retry_count = 0
        for agent_name in affected_agents:
            state.agent_outputs.pop(agent_name, None)
            state.context_usage.pop(agent_name, None)
            state.memory_refs.pop(agent_name, None)
            artifact_id = state.latest_artifacts.pop(agent_name, None)
            if artifact_id:
                state.artifacts.pop(artifact_id, None)
        state.handoffs = [
            handoff for handoff in state.handoffs if handoff.source_agent not in affected_agents
        ]
        return node_ids
