from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model.tool_calling import ModelToolCall
from app.safety.permissions import RiskLevel
from app.safety.policy_gateway import (
    AgentPrincipal,
    ApprovalGrant,
    PolicyBudget,
    PolicyContext,
    PolicyDeniedError,
    ToolPolicyGateway,
)
from app.tools.governed_executor import GovernedToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolSpec


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


class WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


def call(call_id: str, name: str, **arguments) -> ModelToolCall:
    return ModelToolCall(
        call_id=call_id,
        name=name,
        arguments=arguments,
        raw_arguments=json.dumps(arguments),
    )


def main() -> None:
    writes: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="write_demo",
            risk_level=RiskLevel.high,
            side_effect=True,
            requires_approval=True,
            allowed_agents={"browser_agent"},
            input_schema="WriteInput",
        ),
        lambda value: writes.append(value) or {"written": value},
        WriteInput,
        lambda result: None,
    )
    gateway = ToolPolicyGateway(clock=lambda: NOW)
    events: list[dict] = []
    gateway.set_observer(events.append)
    executor = GovernedToolExecutor(registry, gateway)

    strategy = PolicyContext(
        principal=AgentPrincipal(agent_name="strategy_agent", task_id="task_v18"),
        max_risk_level=RiskLevel.low,
        tool_allowlist=frozenset({"calculate_margin", "check_inventory"}),
        budget=PolicyBudget(max_total_calls=2, max_side_effect_calls=0),
    )
    margin = executor.execute(
        call("call_margin", "calculate_margin", price=199, discount=20, cost=95),
        strategy,
    )

    cross_agent_denied = False
    try:
        executor.execute(
            call("call_market", "search_products", category="耳机", audience="学生"),
            strategy,
        )
    except PolicyDeniedError as exc:
        cross_agent_denied = "agent_not_allowed" in exc.decision.reason_codes

    no_approval_denied = False
    browser_without_approval = PolicyContext(
        principal=AgentPrincipal(agent_name="browser_agent", task_id="task_v18"),
        max_risk_level=RiskLevel.high,
        budget=PolicyBudget(max_total_calls=2, max_side_effect_calls=1),
    )
    try:
        executor.execute(
            call("call_write_missing_approval", "write_demo", value="blocked"),
            browser_without_approval,
        )
    except PolicyDeniedError as exc:
        no_approval_denied = "approval_required" in exc.decision.reason_codes

    grant = ApprovalGrant(
        grant_id="grant_v18",
        task_id="task_v18",
        approved_by="v18-acceptance",
        reason="verify one controlled side effect",
        allowed_tools=frozenset({"write_demo"}),
        expires_at=NOW + timedelta(minutes=5),
        max_uses=1,
    )
    browser = browser_without_approval.model_copy(update={"approval": grant})
    write_result = executor.execute(
        call("call_write_allowed", "write_demo", value="approved"), browser
    )
    approval_reuse_denied = False
    try:
        executor.execute(
            call("call_write_reuse", "write_demo", value="must-not-run"), browser
        )
    except PolicyDeniedError as exc:
        approval_reuse_denied = (
            "approval_use_limit_exceeded" in exc.decision.reason_codes
        )

    definitions = executor.definitions_for(strategy)
    policy_trace = [event for event in events if event["event_type"] == "policy_decision"]
    serialized_events = json.dumps(policy_trace, ensure_ascii=False)
    checks = {
        "low_risk_call_allowed": margin.policy_decision.status == "allowed",
        "registry_executed_validated_arguments": margin.result["margin_rate"] == 0.4693,
        "cross_agent_call_denied": cross_agent_denied,
        "denial_did_not_consume_budget": gateway.usage(strategy.principal).total_calls == 1,
        "side_effect_requires_approval": no_approval_denied and writes == ["approved"],
        "scoped_approval_allows_one_write": write_result.result == {"written": "approved"},
        "approval_cannot_be_reused": approval_reuse_denied,
        "model_only_sees_session_tools": [item.name for item in definitions]
        == ["calculate_margin", "check_inventory"],
        "policy_trace_records_allow_and_deny": {event["status"] for event in policy_trace}
        == {"allowed", "denied"},
        "policy_trace_redacts_arguments": "must-not-run" not in serialized_events
        and "discount" not in serialized_events,
    }
    report = {
        "version": "v18",
        "passed": all(checks.values()),
        "evidence_mode": "offline_governed_execution",
        "live_model_called": False,
        "checks": checks,
        "allowed_decisions": sum(event["status"] == "allowed" for event in policy_trace),
        "denied_decisions": sum(event["status"] == "denied" for event in policy_trace),
        "side_effects_applied": len(writes),
        "boundary": (
            "V18 governs ModelToolCall execution in process. Agent identity is a trusted "
            "orchestrator claim, and budget/approval ledgers are not yet durable services."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
