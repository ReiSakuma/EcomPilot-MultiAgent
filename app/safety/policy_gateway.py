from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.model.tool_calling import ModelToolCall
from app.safety.permissions import RiskLevel
from app.tools.schemas import ToolSpec


_RISK_RANK = {RiskLevel.low: 0, RiskLevel.medium: 1, RiskLevel.high: 2}


class AgentPrincipal(BaseModel):
    """Identity claims supplied by the trusted orchestration layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_name: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    tenant_id: str = Field(default="demo", min_length=1)


class ApprovalGrant(BaseModel):
    """Task- and tool-scoped approval instead of a reusable boolean."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: str = Field(default_factory=lambda: f"grant_{uuid4().hex[:12]}")
    task_id: str = Field(min_length=1)
    tenant_id: str = Field(default="demo", min_length=1)
    approved_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    allowed_tools: frozenset[str] = Field(min_length=1)
    expires_at: datetime
    max_uses: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def require_timezone(self) -> ApprovalGrant:
        if self.expires_at.tzinfo is None:
            raise ValueError("Approval expires_at must include a timezone")
        return self


class PolicyBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_total_calls: int = Field(default=8, ge=1, le=1000)
    max_side_effect_calls: int = Field(default=1, ge=0, le=100)


class PolicyContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    principal: AgentPrincipal
    max_risk_level: RiskLevel = RiskLevel.low
    tool_allowlist: frozenset[str] = Field(default_factory=frozenset)
    budget: PolicyBudget = Field(default_factory=PolicyBudget)
    approval: ApprovalGrant | None = None


class BudgetUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    total_calls: int = 0
    side_effect_calls: int = 0


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(default_factory=lambda: f"policy_{uuid4().hex[:12]}")
    status: Literal["allowed", "denied"]
    call_id: str
    tool_name: str
    agent_name: str
    task_id: str
    tenant_id: str
    risk_level: RiskLevel
    side_effect: bool
    requires_approval: bool
    approved_by: str | None = None
    reason_codes: tuple[str, ...] = ()
    budget_before: BudgetUsage
    budget_after: BudgetUsage
    decided_at: datetime


class PolicyDeniedError(PermissionError):
    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        reasons = ", ".join(decision.reason_codes) or "policy_denied"
        super().__init__(
            f"Policy denied agent '{decision.agent_name}' calling "
            f"'{decision.tool_name}': {reasons}"
        )


class ToolPolicyGateway:
    """Atomically authorizes model-selected calls and reserves their budgets."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        self._usage: dict[tuple[str, str, str], BudgetUsage] = {}
        self._seen_call_ids: dict[tuple[str, str, str], set[str]] = {}
        self._grant_uses: dict[str, int] = {}
        self.decisions: list[PolicyDecision] = []
        self._observer: Callable[[dict[str, Any]], None] | None = None

    def set_observer(self, observer: Callable[[dict[str, Any]], None] | None) -> None:
        self._observer = observer

    def usage(self, principal: AgentPrincipal) -> BudgetUsage:
        with self._lock:
            return self._usage.get(self._key(principal), BudgetUsage())

    def authorize_many(
        self,
        calls: list[ModelToolCall],
        context: PolicyContext,
        specs: dict[str, ToolSpec],
    ) -> list[PolicyDecision]:
        if not calls:
            return []
        with self._lock:
            return self._authorize_many_locked(calls, context, specs)

    def _authorize_many_locked(
        self,
        calls: list[ModelToolCall],
        context: PolicyContext,
        specs: dict[str, ToolSpec],
    ) -> list[PolicyDecision]:
        principal = context.principal
        key = self._key(principal)
        before = self._usage.get(key, BudgetUsage())
        seen = self._seen_call_ids.get(key, set())
        batch_ids: set[str] = set()
        grant_uses = self._grant_uses.get(
            context.approval.grant_id, 0
        ) if context.approval else 0
        prospective_total = before.total_calls
        prospective_side_effects = before.side_effect_calls
        prospective_grant_uses = grant_uses
        prepared: list[tuple[ModelToolCall, ToolSpec, bool]] = []

        for call in calls:
            spec = specs.get(call.name)
            if spec is None:
                self._deny(
                    call,
                    context,
                    RiskLevel.low,
                    False,
                    False,
                    ("unknown_tool",),
                    before,
                )
            assert spec is not None
            requires_approval = bool(
                spec.requires_approval or spec.side_effect or spec.risk_level == RiskLevel.high
            )
            reasons: list[str] = []
            if call.call_id in seen or call.call_id in batch_ids:
                reasons.append("duplicate_call_id")
            if spec.allowed_agents and principal.agent_name not in spec.allowed_agents:
                reasons.append("agent_not_allowed")
            if context.tool_allowlist and call.name not in context.tool_allowlist:
                reasons.append("tool_not_in_session_allowlist")
            if _RISK_RANK[spec.risk_level] > _RISK_RANK[context.max_risk_level]:
                reasons.append("risk_level_exceeded")

            prospective_total += 1
            if spec.side_effect:
                prospective_side_effects += 1
            if prospective_total > context.budget.max_total_calls:
                reasons.append("total_call_budget_exceeded")
            if prospective_side_effects > context.budget.max_side_effect_calls:
                reasons.append("side_effect_budget_exceeded")

            if requires_approval:
                approval_reasons = self._approval_reasons(call, context)
                reasons.extend(approval_reasons)
                if not approval_reasons and context.approval is not None:
                    prospective_grant_uses += 1
                    if prospective_grant_uses > context.approval.max_uses:
                        reasons.append("approval_use_limit_exceeded")

            if reasons:
                self._deny(
                    call,
                    context,
                    spec.risk_level,
                    spec.side_effect,
                    requires_approval,
                    tuple(dict.fromkeys(reasons)),
                    before,
                )
            batch_ids.add(call.call_id)
            prepared.append((call, spec, requires_approval))

        decisions: list[PolicyDecision] = []
        running = before
        now = self._now()
        for call, spec, requires_approval in prepared:
            after = BudgetUsage(
                total_calls=running.total_calls + 1,
                side_effect_calls=running.side_effect_calls + int(spec.side_effect),
            )
            decision = PolicyDecision(
                status="allowed",
                call_id=call.call_id,
                tool_name=call.name,
                agent_name=principal.agent_name,
                task_id=principal.task_id,
                tenant_id=principal.tenant_id,
                risk_level=spec.risk_level,
                side_effect=spec.side_effect,
                requires_approval=requires_approval,
                approved_by=context.approval.approved_by
                if requires_approval and context.approval
                else None,
                reason_codes=("policy_allowed",),
                budget_before=running,
                budget_after=after,
                decided_at=now,
            )
            decisions.append(decision)
            running = after

        self._usage[key] = running
        self._seen_call_ids.setdefault(key, set()).update(batch_ids)
        if context.approval is not None:
            self._grant_uses[context.approval.grant_id] = prospective_grant_uses
        for decision in decisions:
            self._record(decision)
        return decisions

    def _approval_reasons(
        self, call: ModelToolCall, context: PolicyContext
    ) -> list[str]:
        grant = context.approval
        if grant is None:
            return ["approval_required"]
        principal = context.principal
        reasons: list[str] = []
        if grant.task_id != principal.task_id:
            reasons.append("approval_task_mismatch")
        if grant.tenant_id != principal.tenant_id:
            reasons.append("approval_tenant_mismatch")
        if call.name not in grant.allowed_tools:
            reasons.append("approval_tool_scope_mismatch")
        if grant.expires_at <= self._now():
            reasons.append("approval_expired")
        return reasons

    def _deny(
        self,
        call: ModelToolCall,
        context: PolicyContext,
        risk_level: RiskLevel,
        side_effect: bool,
        requires_approval: bool,
        reasons: tuple[str, ...],
        usage: BudgetUsage,
    ) -> None:
        principal = context.principal
        decision = PolicyDecision(
            status="denied",
            call_id=call.call_id,
            tool_name=call.name,
            agent_name=principal.agent_name,
            task_id=principal.task_id,
            tenant_id=principal.tenant_id,
            risk_level=risk_level,
            side_effect=side_effect,
            requires_approval=requires_approval,
            approved_by=None,
            reason_codes=reasons,
            budget_before=usage,
            budget_after=usage,
            decided_at=self._now(),
        )
        self._record(decision)
        raise PolicyDeniedError(decision)

    def _record(self, decision: PolicyDecision) -> None:
        self.decisions.append(decision)
        if self._observer is None:
            return
        try:
            self._observer(
                {
                    "event_type": "policy_decision",
                    "component_type": "policy",
                    "component_name": "tool_policy_gateway",
                    "agent_name": decision.agent_name,
                    "step": "tool.authorize",
                    "status": decision.status,
                    "details": decision.model_dump(mode="json"),
                    "error": None,
                }
            )
        except Exception:
            return

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Policy clock must return a timezone-aware datetime")
        return value

    @staticmethod
    def _key(principal: AgentPrincipal) -> tuple[str, str, str]:
        return principal.tenant_id, principal.task_id, principal.agent_name
