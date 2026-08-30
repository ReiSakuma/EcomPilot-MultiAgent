from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TerminalClass = Literal[
    "success",
    "waiting_user",
    "business_rejected",
    "degraded_completed",
    "manual_attention",
]
FaultType = Literal[
    "network_partition",
    "database_failover",
    "duplicate_delivery",
    "model_rate_limit",
    "slow_tool",
    "summary_pollution",
]


class TerminalOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    terminal_class: TerminalClass
    source_outcome: str
    reason: str
    recoverable: bool = False
    human_action: str | None = None
    degradation_refs: tuple[str, ...] = ()
    failure_code: str | None = None


class ChaosScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fault: FaultType
    injection_point: str
    attempts: int = Field(ge=1)
    recovered: bool
    terminal_class: TerminalClass
    expected_control: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    passed: bool


class ChaosReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    release: Literal["v39-chaos-readiness"] = "v39-chaos-readiness"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scenarios: tuple[ChaosScenario, ...]
    recovered_scenarios: int
    total_scenarios: int
    passed: bool
    boundary: str


class CapacityReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    jobs: int
    workers: int
    enqueue_throughput_per_second: float
    drain_throughput_per_second: float
    enqueue_p50_ms: float
    enqueue_p95_ms: float
    duplicate_jobs: int
    dead_jobs: int
    recommended_worker_counts: dict[str, int]
    passed: bool
    boundary: str


class IsolationAudit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    checks: dict[str, bool]
    cross_tenant_leaks: int = Field(ge=0)
    passed: bool


class SloIndicator(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: float
    objective: str
    passed: bool
    severity: Literal["page", "ticket", "none"] = "none"


class SloReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    indicators: tuple[SloIndicator, ...]
    alerts: tuple[str, ...]
    passed: bool


class OperationalReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    release: Literal["v39-chaos-readiness"] = "v39-chaos-readiness"
    status: Literal["reference_validated", "needs_validation"]
    five_terminal_states_covered: bool
    chaos: ChaosReport
    capacity: CapacityReport
    isolation: IsolationAudit
    slo: SloReport
    production_claimed: Literal[False] = False
    production_boundaries: tuple[str, ...]
