from __future__ import annotations

from app.agents.supervisor import Supervisor
from app.orchestration.state import TaskState
from app.safety.approval import Approval


def run_workflow(
    goal: str,
    approved: bool = False,
    approved_by: str | None = None,
    approval_reason: str | None = None,
) -> TaskState:
    return Supervisor().run(
        goal,
        approved=approved,
        approved_by=approved_by,
        approval_reason=approval_reason,
    )


def resume_workflow(
    task_id: str,
    *,
    approval: Approval | None = None,
    retry_node: str | None = None,
    constraint_updates: dict | None = None,
    expected_checkpoint_version: int | None = None,
    requested_by: str | None = None,
    reason: str | None = None,
    turn_id: str | None = None,
) -> TaskState:
    return Supervisor().resume(
        task_id,
        approval=approval,
        retry_node=retry_node,
        constraint_updates=constraint_updates,
        expected_checkpoint_version=expected_checkpoint_version,
        requested_by=requested_by,
        reason=reason,
        turn_id=turn_id,
    )
