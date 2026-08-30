from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    tenant_id: str
    title: str
    status: Literal["active", "archived"] = "active"
    active_product_id: str | None = None
    active_task_session_id: str | None = None
    summary: str = ""
    summary_version: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class MessageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    conversation_id: str
    turn_id: str
    tenant_id: str
    role: Literal["user", "assistant"]
    content: str
    intent: str = "create_listing"
    task_id: str | None = None
    product_refs: list[str] = Field(default_factory=list)
    created_at: datetime


class TurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    conversation_id: str
    tenant_id: str
    ordinal: int = Field(ge=1)
    client_request_id: str
    status: Literal["processing", "completed", "failed"]
    task_id: str | None = None
    error_code: str | None = None
    response_payload: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None


class TaskIndexRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    conversation_id: str
    turn_id: str
    tenant_id: str
    intent: str
    outcome: str
    run_id: str
    created_at: datetime
    updated_at: datetime


class ConversationSummary(ConversationRecord):
    last_message: str = ""
    last_task_status: str | None = None
    message_count: int = Field(default=0, ge=0)


class ConversationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: ConversationRecord
    messages: list[MessageRecord]
    turns: list[TurnRecord]
    tasks: list[TaskIndexRecord]
    task_sessions: list[TaskSessionRecord] = Field(default_factory=list)
    workflow_runs: list[WorkflowRunRecord] = Field(default_factory=list)
    batch_jobs: list[BatchJobRecord] = Field(default_factory=list)
    batch_items: list[BatchItemRecord] = Field(default_factory=list)


class TurnReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: bool
    conversation: ConversationRecord
    turn: TurnRecord


class PendingRequestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    tenant_id: str
    task_session_id: str | None = None
    checkpoint_thread_id: str | None = None
    status: Literal["waiting_for_input", "suspended", "resolved", "advisory"]
    compiled_payload: dict[str, Any]
    clarification_round: int = Field(ge=0, le=3)
    last_question: str
    created_at: datetime
    updated_at: datetime


TaskSessionStatus = Literal[
    "created",
    "running",
    "waiting_for_input",
    "awaiting_approval",
    "suspended",
    "completed",
    "business_rejected",
    "technical_failed",
    "needs_attention",
    "cancelled",
]

WorkflowRunStatus = Literal[
    "created",
    "queued",
    "running",
    "waiting_for_input",
    "awaiting_approval",
    "suspended",
    "completed",
    "business_rejected",
    "technical_failed",
    "needs_attention",
    "cancelled",
]

BatchJobStatus = Literal[
    "created",
    "running",
    "waiting_for_input",
    "awaiting_approval",
    "partially_completed",
    "completed",
    "failed",
    "cancelled",
]


class TaskSessionRecord(BaseModel):
    """Long-lived business task identity inside one conversation."""

    model_config = ConfigDict(extra="forbid")

    task_session_id: str
    tenant_id: str
    conversation_id: str
    origin_turn_id: str
    batch_job_id: str | None = None
    intent: str
    title: str
    status: TaskSessionStatus = "created"
    active_workflow_run_id: str | None = None
    checkpoint_thread_id: str
    current_task_id: str | None = None
    entity_refs: list[str] = Field(default_factory=list)
    state_version: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class WorkflowRunRecord(BaseModel):
    """One execution attempt belonging to a task session."""

    model_config = ConfigDict(extra="forbid")

    workflow_run_id: str
    task_session_id: str
    tenant_id: str
    conversation_id: str
    trigger_turn_id: str
    task_id: str | None = None
    run_id: str | None = None
    parent_workflow_run_id: str | None = None
    status: WorkflowRunStatus = "created"
    checkpoint_thread_id: str
    checkpoint_namespace: str = "default"
    attempt: int = Field(default=1, ge=1)
    execution_epoch: int = Field(default=1, ge=1)
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class BatchJobRecord(BaseModel):
    """Durable aggregate for independently planned and executed child tasks."""

    model_config = ConfigDict(extra="forbid")

    batch_job_id: str
    tenant_id: str
    conversation_id: str
    origin_turn_id: str
    operation: str
    status: BatchJobStatus = "created"
    item_count: int = Field(default=0, ge=0)
    completed_count: int = Field(default=0, ge=0)
    executed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class BatchItemRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    batch_job_id: str
    item_id: str
    task_session_id: str
    label: str
    status: Literal[
        "created", "ready", "waiting_for_input", "running",
        "awaiting_approval", "completed", "failed", "skipped", "needs_attention"
    ] = "created"
    request_payload: dict[str, Any]
    task_id: str | None = None
    error_code: str | None = None
    execution_attempts: int = Field(default=0, ge=0, le=3)
    execution_history: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TurnTaskLinkRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    turn_id: str
    task_session_id: str
    relation: Literal["created", "continued", "recalled", "switched", "batch_item"]
    linked_at: datetime


TaskRelation = Literal[
    "new_task", "continue_task", "recall_task", "switch_task", "general_message"
]


class TaskRouteDecision(BaseModel):
    """Typed decision that binds one user turn to at most one task session."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    relation: TaskRelation
    target_task_session_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str
    evidence: list[str] = Field(default_factory=list, max_length=8)
    source: Literal["deterministic", "model", "fallback"] = "deterministic"


class TurnTaskRouteRecord(TaskRouteDecision):
    tenant_id: str
    conversation_id: str
    turn_id: str
    created_at: datetime
