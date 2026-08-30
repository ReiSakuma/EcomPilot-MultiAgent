from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.safety.permissions import RiskLevel


class ToolSpec(BaseModel):
    protocol_version: Literal["2.0"] = "2.0"
    name: str
    required_args: set[str] = Field(default_factory=set)
    risk_level: RiskLevel = RiskLevel.low
    side_effect: bool = False
    timeout_seconds: float = 10
    max_retries: int = Field(default=1, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.05, ge=0, le=5)
    requires_approval: bool = False
    allowed_agents: set[str] = Field(default_factory=set)
    input_schema: str = ""
    operation_type: Literal["read", "write"] = "read"
    idempotency: Literal["safe", "keyed", "none"] = "safe"
    retryable_errors: set[str] = Field(
        default_factory=lambda: {"transient", "rate_limit", "concurrency_conflict"}
    )
    fallback_tool: str | None = None
    result_schema: str = "validated"
    compensation: str = "not_required"
    reconcile_tool: str | None = None
    tenant_scoped: bool = True
    concurrency_limit: int = Field(default=8, ge=1, le=100)
    circuit_failure_threshold: int = Field(default=3, ge=2, le=10)


class ToolCallRecord(BaseModel):
    call_id: str = ""
    tool_name: str
    args: dict[str, Any]
    status: str
    risk_level: RiskLevel
    side_effect: bool
    agent_name: str = "unknown"
    task_id: str = ""
    tenant_id: str = "tenant_demo"
    delegation_id: str | None = None
    capability_id: str | None = None
    capability_token_id: str | None = None
    approved_by: str | None = None
    started_at: datetime | None = None
    duration_ms: float | None = None
    attempt_count: int = 1
    validation_status: str = "not_run"
    idempotent_replay: bool = False
    recovered_result: bool = False
    input_hash: str = ""
    output_hash: str | None = None
    failure_category: str | None = None
    error_signature: str | None = None
    circuit_state: str = "closed"
    retry_decisions: list[dict[str, Any]] = Field(default_factory=list)
    result_summary: Any = None
    error_type: str | None = None
    error: str | None = None


class ToolValidationError(ValueError):
    pass


class ToolParameterError(ToolValidationError):
    pass


class ToolResultValidationError(ToolValidationError):
    pass


class ToolTimeoutError(TimeoutError):
    pass


class TransientToolError(RuntimeError):
    pass


class UnknownWriteStateError(ToolTimeoutError):
    """A write timed out and must be read back before it can be retried."""

    safe_to_retry = False
