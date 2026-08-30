from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.access.models import AccessPrincipal, default_principal
from app.orchestration.handoff import Handoff
from app.orchestration.artifacts import Artifact
from app.orchestration.failures import (
    AgentOutputContractError,
    FailureEnvelope,
    TaskOutcome,
)
from app.orchestration.a2a import (
    A2ABudget,
    A2ADelegationRecord,
    A2ATransitionEvent,
)
from app.reliability.models import ExecutionReceipt, RetryBudget


class NodeStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class TaskNode(BaseModel):
    node_id: str
    agent_name: str
    dependencies: list[str] = Field(default_factory=list)
    capability_id: str = ""
    supplemental_artifact_refs: list[str] = Field(default_factory=list)
    delegation_id: str | None = None
    status: NodeStatus = NodeStatus.pending
    retry_count: int = 0
    max_retries: int = 1


class WorkflowLoopState(BaseModel):
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=2, ge=1, le=3)
    phase: Literal[
        "revision_pending", "review_pending", "completed", "exhausted"
    ] = "revision_pending"
    feedback: list[dict[str, Any]] = Field(default_factory=list)
    source_artifact_ref: str | None = None
    revised_artifact_ref: str | None = None
    target_agents: list[str] = Field(default_factory=list)
    completed_agents: list[str] = Field(default_factory=list)
    revised_artifact_refs: dict[str, str] = Field(default_factory=dict)
    finding_fingerprint: str | None = None
    safe_finalize: bool = False
    stop_reason: str | None = None


class RecoveryRecord(BaseModel):
    from_run_id: str
    to_run_id: str
    action: str
    restarted_nodes: list[str] = Field(default_factory=list)
    checkpoint_version: int
    requested_by: str | None = None
    reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskState(BaseModel):
    schema_version: Literal["1.0", "1.1"] = "1.1"
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:8]}")
    run_id: str = ""
    goal: str
    conversation_id: str | None = None
    turn_id: str | None = None
    intent: str = "create_listing"
    route_plan: dict[str, Any] = Field(default_factory=dict)
    entity_refs: list[str] = Field(default_factory=list)
    principal: AccessPrincipal = Field(default_factory=default_principal)
    constraints: dict[str, Any] = Field(default_factory=dict)
    todo: list[str] = Field(default_factory=list)
    nodes: dict[str, TaskNode] = Field(default_factory=dict)
    handoffs: list[Handoff] = Field(default_factory=list)
    agent_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    latest_artifacts: dict[str, str] = Field(default_factory=dict)
    a2a_budget: A2ABudget = Field(default_factory=A2ABudget)
    a2a_delegations: dict[str, A2ADelegationRecord] = Field(default_factory=dict)
    a2a_events: list[A2ATransitionEvent] = Field(default_factory=list)
    state_version: int = 0
    context_seed: dict[str, Any] = Field(default_factory=dict)
    context_usage: dict[str, dict[str, Any]] = Field(default_factory=dict)
    memory_refs: dict[str, list[str]] = Field(default_factory=dict)
    tool_records: list[dict[str, Any]] = Field(default_factory=list)
    model_records: list[dict[str, Any]] = Field(default_factory=list)
    model_fallbacks: list[dict[str, Any]] = Field(default_factory=list)
    protocol_migrations: list[dict[str, Any]] = Field(default_factory=list)
    degradations: list[FailureEnvelope] = Field(default_factory=list)
    failure_history: list[FailureEnvelope] = Field(default_factory=list)
    failure: FailureEnvelope | None = None
    retry_budget: RetryBudget = Field(default_factory=RetryBudget)
    execution_receipts: dict[str, ExecutionReceipt] = Field(default_factory=dict)
    reliability_events: list[dict[str, Any]] = Field(default_factory=list)
    needs_attention: bool = False
    outcome: TaskOutcome = TaskOutcome.created
    workflow_loops: dict[str, WorkflowLoopState] = Field(default_factory=dict)
    approved: bool = False
    approved_by: str | None = None
    approval_reason: str | None = None
    checkpoint_version: int = 0
    resume_count: int = 0
    parent_run_id: str | None = None
    recovery_history: list[RecoveryRecord] = Field(default_factory=list)
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def mark_updated(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def require_agent_output(
        self, agent_name: str, *, required_keys: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        output = self.agent_outputs.get(agent_name)
        if output is None:
            raise AgentOutputContractError(
                f"Required output from '{agent_name}' is missing"
            )
        missing = [key for key in required_keys if key not in output]
        if missing:
            raise AgentOutputContractError(
                f"Output from '{agent_name}' is missing required fields: {missing}"
            )
        return output

    def record_failure(self, failure: FailureEnvelope) -> None:
        self.failure = failure
        self.failure_history.append(failure)
        self.outcome = (
            TaskOutcome.business_rejected
            if failure.category == "business_rule"
            else TaskOutcome.technical_failed
        )
        self.mark_updated()
