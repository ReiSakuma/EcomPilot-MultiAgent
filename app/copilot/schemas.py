from __future__ import annotations

from enum import Enum
from typing import Any, Literal
import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.orchestration.failures import FailureEnvelope, TaskOutcome
from app.conversations.models import ConversationDetail, ConversationRecord, ConversationSummary
from app.copilot.intents import IntentDecision, RequestAssessment, RoutePlan


class CopilotOutcome(str, Enum):
    created = "created"
    running = "running"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    business_rejected = "business_rejected"
    technical_failed = "technical_failed"
    waiting_for_input = "waiting_for_input"
    advisory = "advisory"
    read_only_completed = "read_only_completed"
    answered = "answered"
    out_of_scope = "out_of_scope"


class ActionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    title: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    detail: str
    agent_name: str
    tool_names: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    trace_refs: list[str] = Field(default_factory=list)


class ActionSummary(BaseModel):
    """Deterministic user-facing projection of recorded workflow activity."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    steps: list[ActionStep]
    completed_step_count: int = Field(ge=0)
    total_step_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    trace_event_count: int = Field(ge=0)
    execution_performed: bool


class PanelDescriptor(BaseModel):
    """One stable business panel; the UI does not read raw TaskState internals."""

    model_config = ConfigDict(extra="forbid")

    panel_id: Literal[
        "requirements", "market", "listing", "strategy", "review", "execution",
        "product", "timeline", "analytics", "memory"
    ]
    title: str
    status: Literal["ready", "waiting", "completed", "blocked", "failed", "not_run"]
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    source_agents: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)


class ModelUsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured_provider: str
    configured_model: str
    recorded_call_count: int = Field(ge=0)
    actual_call_count: int = Field(ge=0)
    stub_call_count: int = Field(ge=0)
    mode: Literal["real_model", "test_stub", "no_model_call"]
    providers_used: list[str] = Field(default_factory=list)


class PriceConfirmationOption(BaseModel):
    """One user-safe recovery choice; it never contains executable internals."""

    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "adopt_suggested_price",
        "keep_original_with_evidence",
        "market_analysis_only",
    ]
    label: str
    description: str
    requires_evidence: bool = False
    suggested_price: float | None = Field(default=None, gt=0)


class PriceConfirmationPrompt(BaseModel):
    """Versioned presentation contract for the v55 price-confirmation surface."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    task_id: str
    run_id: str
    checkpoint_version: int = Field(ge=0)
    position: Literal[
        "below_market", "within_market", "above_market", "cost_market_conflict", "unavailable"
    ]
    target_price: float = Field(gt=0)
    core_reference_price: float | None = Field(default=None, gt=0)
    deviation_rate: float | None = None
    acceptance_band: tuple[float, float] | None = None
    suggested_price_range: tuple[float, float] | None = None
    core_price_band: tuple[float, float] | None = None
    adjacent_price_band: tuple[float, float] | None = None
    full_market_band: tuple[float, float] | None = None
    evidence_quality: Literal["high", "medium", "low", "unavailable"]
    core_sample_count: int = Field(ge=0)
    adjacent_sample_count: int = Field(ge=0)
    full_market_sample_count: int = Field(ge=0)
    excluded_sample_count: int = Field(ge=0)
    options: list[PriceConfirmationOption] = Field(min_length=3, max_length=3)


class CopilotResponse(BaseModel):
    """Versioned contract shared by the conversation UI and API clients."""

    model_config = ConfigDict(extra="forbid")

    # V34 binds UI approval and dynamic panels to one versioned product contract.
    protocol_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"] = "1.7"
    response_id: str = Field(default_factory=lambda: f"response_{uuid4().hex[:12]}")
    conversation_id: str | None = None
    turn_id: str | None = None
    thread_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    outcome: CopilotOutcome
    intent: IntentDecision | None = None
    assessment: RequestAssessment | None = None
    route_plan: RoutePlan | None = None
    data_scope: list[str] = Field(default_factory=list)
    entity_refs: list[str] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    assistant_message: str
    understood_requirements: dict[str, Any] = Field(default_factory=dict)
    action_summary: ActionSummary
    panels: list[PanelDescriptor]
    model_usage: ModelUsageSummary
    price_confirmation: PriceConfirmationPrompt | None = None
    approval_required: bool
    approval_state: Literal["not_required", "waiting", "approved", "executed"] = "not_required"
    execution_plan_hash: str | None = None
    store_modified: bool
    failure: FailureEnvelope | None = None
    links: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_approval_to_rendered_plan(self) -> "CopilotResponse":
        if self.approval_required:
            self.approval_state = "waiting"
            if not self.execution_plan_hash:
                material = {
                    "task_id": self.task_id,
                    "panels": [
                        panel.model_dump(mode="json")
                        for panel in self.panels
                        if panel.panel_id in {"listing", "strategy", "review"}
                    ],
                }
                encoded = json.dumps(
                    material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                self.execution_plan_hash = hashlib.sha256(encoded).hexdigest()
        elif self.store_modified:
            self.approval_state = "executed"
        return self


class CopilotEvent(BaseModel):
    """Persisted, user-safe event. Hidden model reasoning is never stored here."""

    model_config = ConfigDict(extra="forbid")

    event_id: int = Field(ge=1)
    stream_id: str
    event_type: Literal[
        "request_received", "intent_recognized", "route_planned",
        "agent_started", "agent_completed", "model_completed", "tool_completed",
        "review_revised", "stage_failed", "approval_waiting",
        "execution_completed", "response_ready", "stream_failed", "heartbeat"
    ]
    stage: str
    status: Literal["running", "completed", "waiting", "failed"]
    title: str
    detail: str
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CopilotDispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream_id: str
    conversation_id: str
    status: Literal["running"] = "running"
    events_url: str


class ActiveStreamResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream_id: str
    conversation_id: str
    status: Literal["running"] = "running"
    events_url: str
    last_event_id: int = Field(ge=0)


class ConversationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversations: list[ConversationSummary]


class ConversationDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: ConversationDetail
    latest_response: CopilotResponse | None = None
