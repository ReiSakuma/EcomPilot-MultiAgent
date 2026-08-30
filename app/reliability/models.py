from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class FailureTaxonomy(str, Enum):
    transient = "transient"
    rate_limit = "rate_limit"
    schema_invalid = "schema_invalid"
    business_rule = "business_rule"
    permission_denied = "permission_denied"
    concurrency_conflict = "concurrency_conflict"
    permanent = "permanent"
    unknown = "unknown"


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: Literal["1.0"] = "1.0"
    category: FailureTaxonomy
    max_attempts: int = Field(ge=1, le=4)
    backoff_seconds: tuple[float, ...] = ()
    jitter_ratio: float = Field(default=0.15, ge=0, le=1)
    respect_retry_after: bool = False
    action: Literal[
        "retry", "repair_then_retry", "reread_then_retry", "fail", "quarantine"
    ]


class RetryDecision(BaseModel):
    policy_version: Literal["1.0"] = "1.0"
    component: str
    category: FailureTaxonomy
    error_signature: str
    attempt: int = Field(ge=1)
    allowed: bool
    delay_seconds: float = Field(default=0, ge=0)
    reason: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RetryBudget(BaseModel):
    """One bounded retry allowance shared by every layer of one task."""

    max_attempts: int = Field(default=8, ge=0, le=30)
    consumed: int = Field(default=0, ge=0)
    decisions: list[RetryDecision] = Field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.max_attempts - self.consumed)

    def consume(self, decision: RetryDecision) -> bool:
        if not decision.allowed or self.remaining <= 0:
            return False
        self.consumed += 1
        self.decisions.append(decision)
        return True


class CircuitSnapshot(BaseModel):
    key: str
    state: Literal["closed", "open", "half_open"] = "closed"
    consecutive_failures: int = 0
    threshold: int = 3
    opened_at: datetime | None = None
    last_signature: str | None = None


class DeadLetterRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: f"dlq_{uuid4().hex[:12]}")
    task_id: str
    run_id: str = ""
    tenant_id: str
    stage: str
    agent_name: str | None = None
    tool_name: str | None = None
    category: FailureTaxonomy
    error_signature: str
    user_message: str
    developer_message: str
    checkpoint_version: int = 0
    status: Literal["needs_attention", "resolved", "dismissed"] = "needs_attention"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionReceipt(BaseModel):
    tool_name: str
    input_hash: str
    output_hash: str | None = None
    status: Literal["completed", "failed", "unknown"]
    side_effect: bool = False
    reusable: bool = False
    result: Any = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
