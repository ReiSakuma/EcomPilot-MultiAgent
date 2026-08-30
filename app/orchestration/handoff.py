from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.orchestration.artifacts import Artifact
from app.orchestration.failures import FailureEnvelope


AgentName = Literal[
    "supervisor",
    "market_agent",
    "market_price_gate_agent",
    "listing_agent",
    "strategy_agent",
    "review_agent",
    "browser_agent",
    "analytics_agent",
]


class Handoff(BaseModel):
    protocol_version: Literal["1.0", "1.1"] = "1.1"
    task_id: str
    source_agent: AgentName
    target_agent: AgentName
    status: Literal[
        "completed", "failed", "requires_review", "requires_revision", "requires_input"
    ] = "completed"
    result: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    evidence_refs: list[str] = Field(default_factory=list)
    error: str | None = None
    failure: FailureEnvelope | None = None
    delegation_id: str | None = None
    input_artifact_refs: tuple[str, ...] = ()
    artifact: Artifact | None = None
    state_delta: "AgentStateDelta" = Field(default_factory=lambda: AgentStateDelta())


class AgentStateDelta(BaseModel):
    context_usage: dict[str, Any] | None = None
    memory_refs: tuple[str, ...] = ()
    model_records: tuple[dict[str, Any], ...] = ()
    model_fallbacks: tuple[dict[str, Any], ...] = ()
    degradations: tuple[FailureEnvelope, ...] = ()


Handoff.model_rebuild()
