from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.orchestration.failures import FailureEnvelope, TaskOutcome
from app.orchestration.state import TaskState


class TaskPresentation(BaseModel):
    """Stable read model consumed by user and operations interfaces."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = "1.0"
    task_id: str
    run_id: str
    checkpoint_version: int
    outcome: TaskOutcome
    legacy_status: str
    failure: FailureEnvelope | None = None
    degradations: list[FailureEnvelope] = Field(default_factory=list)
    stages: dict[str, str]
    model_call_count: int
    tool_call_count: int
    store_modified: bool


def build_task_presentation(state: TaskState) -> TaskPresentation:
    outcome = _effective_outcome(state)
    browser = state.agent_outputs.get("browser_agent", {})
    verification = browser.get("verification", {})
    return TaskPresentation(
        task_id=state.task_id,
        run_id=state.run_id,
        checkpoint_version=state.checkpoint_version,
        outcome=outcome,
        legacy_status=state.status,
        failure=state.failure,
        degradations=list(state.degradations),
        stages={node_id: node.status.value for node_id, node in state.nodes.items()},
        model_call_count=len(state.model_records),
        tool_call_count=len(state.tool_records),
        store_modified=bool(
            outcome is TaskOutcome.completed and verification.get("verified")
        ),
    )


def state_response(state: TaskState) -> dict[str, Any]:
    return state.model_dump(mode="json") | {
        "presentation": build_task_presentation(state).model_dump(mode="json")
    }


def _effective_outcome(state: TaskState) -> TaskOutcome:
    if state.outcome is not TaskOutcome.created or state.status == "created":
        return state.outcome
    return {
        "running": TaskOutcome.running,
        "recovering": TaskOutcome.running,
        "waiting_for_approval": TaskOutcome.awaiting_approval,
        "waiting_for_input": TaskOutcome.waiting_for_input,
        "completed": TaskOutcome.completed,
        "failed": (
            TaskOutcome.business_rejected
            if state.failure and state.failure.category == "business_rule"
            else TaskOutcome.technical_failed
        ),
    }.get(state.status, TaskOutcome.technical_failed)
