from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict, Field

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


def model_call(call_id: str, name: str, **arguments) -> ModelToolCall:
    return ModelToolCall(
        call_id=call_id,
        name=name,
        arguments=arguments,
        raw_arguments=json.dumps(arguments),
    )


def strategy_context(
    *,
    task_id: str = "task_policy",
    budget: PolicyBudget | None = None,
    allowlist: frozenset[str] = frozenset(),
) -> PolicyContext:
    return PolicyContext(
        principal=AgentPrincipal(agent_name="strategy_agent", task_id=task_id),
        max_risk_level=RiskLevel.low,
        tool_allowlist=allowlist,
        budget=budget or PolicyBudget(),
    )


def browser_context(
    *,
    approval: ApprovalGrant | None = None,
    task_id: str = "task_policy",
    budget: PolicyBudget | None = None,
) -> PolicyContext:
    return PolicyContext(
        principal=AgentPrincipal(agent_name="browser_agent", task_id=task_id),
        max_risk_level=RiskLevel.high,
        budget=budget or PolicyBudget(max_total_calls=8, max_side_effect_calls=2),
        approval=approval,
    )


def approval(**updates) -> ApprovalGrant:
    values = {
        "grant_id": "grant_test",
        "task_id": "task_policy",
        "tenant_id": "demo",
        "approved_by": "interviewer",
        "reason": "approve controlled write",
        "allowed_tools": frozenset({"write_demo"}),
        "expires_at": NOW + timedelta(minutes=10),
        "max_uses": 1,
    }
    values.update(updates)
    return ApprovalGrant(**values)


def governed_fixture() -> tuple[GovernedToolExecutor, ToolPolicyGateway, list[str]]:
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
    return GovernedToolExecutor(registry, gateway), gateway, writes


def test_low_risk_model_call_is_authorized_and_executed() -> None:
    executor, gateway, _ = governed_fixture()
    call = model_call("call_margin", "calculate_margin", price=199, discount=20, cost=95)

    outcome = executor.execute(call, strategy_context())

    assert outcome.result["margin_rate"] == 0.4693
    assert outcome.policy_decision.status == "allowed"
    assert gateway.usage(strategy_context().principal).total_calls == 1


def test_definitions_only_expose_tools_allowed_for_agent_and_session() -> None:
    executor, _, _ = governed_fixture()
    context = strategy_context(allowlist=frozenset({"calculate_margin"}))

    definitions = executor.definitions_for(context)

    assert [definition.name for definition in definitions] == ["calculate_margin"]
    assert definitions[0].input_model.model_json_schema()["title"] == "MarginInput"


def test_exported_tool_specs_cannot_mutate_registry_permissions() -> None:
    executor, _, _ = governed_fixture()
    exported = executor.registry.specs()
    exported["calculate_margin"].allowed_agents.clear()

    assert executor.registry.spec("calculate_margin").allowed_agents == {
        "strategy_agent"
    }


def test_agent_allowlist_denies_cross_agent_tool_before_execution() -> None:
    executor, gateway, _ = governed_fixture()

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(
            model_call("call_market", "search_products", category="耳机", audience="学生"),
            strategy_context(),
        )

    assert "agent_not_allowed" in captured.value.decision.reason_codes
    assert gateway.usage(strategy_context().principal).total_calls == 0
    assert executor.registry.records() == []


def test_session_allowlist_is_enforced() -> None:
    executor, _, _ = governed_fixture()
    context = strategy_context(allowlist=frozenset({"check_inventory"}))

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(
            model_call("call_margin", "calculate_margin", price=199, discount=20, cost=95),
            context,
        )

    assert "tool_not_in_session_allowlist" in captured.value.decision.reason_codes


def test_high_risk_side_effect_requires_scoped_approval() -> None:
    executor, _, writes = governed_fixture()

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(
            model_call("call_write", "write_demo", value="draft"), browser_context()
        )

    assert "approval_required" in captured.value.decision.reason_codes
    assert writes == []


def test_session_risk_ceiling_blocks_high_risk_tool_even_with_approval() -> None:
    executor, _, writes = governed_fixture()
    context = browser_context(approval=approval()).model_copy(
        update={"max_risk_level": RiskLevel.low}
    )

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(model_call("call_write", "write_demo", value="draft"), context)

    assert "risk_level_exceeded" in captured.value.decision.reason_codes
    assert writes == []


@pytest.mark.parametrize(
    ("grant", "reason"),
    [
        (approval(task_id="another_task"), "approval_task_mismatch"),
        (approval(tenant_id="another_tenant"), "approval_tenant_mismatch"),
        (approval(allowed_tools=frozenset({"browser_execute"})), "approval_tool_scope_mismatch"),
        (approval(expires_at=NOW), "approval_expired"),
    ],
)
def test_invalid_approval_scope_is_denied(grant: ApprovalGrant, reason: str) -> None:
    executor, _, writes = governed_fixture()

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(
            model_call("call_write", "write_demo", value="draft"),
            browser_context(approval=grant),
        )

    assert reason in captured.value.decision.reason_codes
    assert writes == []


def test_valid_approval_executes_once_and_cannot_be_reused() -> None:
    executor, gateway, writes = governed_fixture()
    context = browser_context(approval=approval())

    first = executor.execute(
        model_call("call_write_1", "write_demo", value="first"), context
    )
    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(
            model_call("call_write_2", "write_demo", value="second"), context
        )

    assert first.policy_decision.approved_by == "interviewer"
    assert "approval_use_limit_exceeded" in captured.value.decision.reason_codes
    assert writes == ["first"]
    assert gateway.usage(context.principal).side_effect_calls == 1


def test_total_and_side_effect_budgets_fail_closed() -> None:
    executor, gateway, writes = governed_fixture()
    context = browser_context(
        approval=approval(max_uses=2),
        budget=PolicyBudget(max_total_calls=2, max_side_effect_calls=1),
    )
    executor.execute(model_call("call_1", "write_demo", value="first"), context)

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(model_call("call_2", "write_demo", value="second"), context)

    assert "side_effect_budget_exceeded" in captured.value.decision.reason_codes
    assert gateway.usage(context.principal).total_calls == 1
    assert writes == ["first"]


def test_total_call_budget_is_reserved_per_task_and_agent() -> None:
    executor, gateway, _ = governed_fixture()
    context = strategy_context(
        budget=PolicyBudget(max_total_calls=1, max_side_effect_calls=0)
    )
    executor.execute(
        model_call("call_1", "calculate_margin", price=199, discount=20, cost=95),
        context,
    )

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(
            model_call("call_2", "check_inventory", inventory=800, planned_units=300),
            context,
        )

    assert "total_call_budget_exceeded" in captured.value.decision.reason_codes
    assert gateway.usage(context.principal).total_calls == 1


def test_duplicate_model_call_id_is_blocked() -> None:
    executor, gateway, _ = governed_fixture()
    context = strategy_context()
    call = model_call("same_call", "calculate_margin", price=199, discount=20, cost=95)
    executor.execute(call, context)

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(call, context)

    assert "duplicate_call_id" in captured.value.decision.reason_codes
    assert gateway.usage(context.principal).total_calls == 1


def test_batch_authorization_is_atomic_when_one_call_is_forbidden() -> None:
    executor, gateway, _ = governed_fixture()
    context = strategy_context()
    calls = [
        model_call("call_ok", "calculate_margin", price=199, discount=20, cost=95),
        model_call("call_forbidden", "search_products", category="耳机", audience="学生"),
    ]

    with pytest.raises(PolicyDeniedError):
        executor.execute_many(calls, context)

    assert gateway.usage(context.principal).total_calls == 0
    assert executor.registry.records() == []


def test_unknown_forged_tool_is_denied_without_registry_access() -> None:
    executor, _, _ = governed_fixture()

    with pytest.raises(PolicyDeniedError) as captured:
        executor.execute(model_call("call_shell", "run_shell", command="id"), strategy_context())

    assert captured.value.decision.reason_codes == ("unknown_tool",)


def test_policy_trace_contains_identity_and_decision_but_not_arguments() -> None:
    executor, gateway, _ = governed_fixture()
    events: list[dict] = []
    gateway.set_observer(events.append)

    executor.execute(
        model_call("call_private", "calculate_margin", price=199, discount=20, cost=95),
        strategy_context(),
    )

    serialized = json.dumps(events[0], ensure_ascii=False)
    assert events[0]["event_type"] == "policy_decision"
    assert events[0]["agent_name"] == "strategy_agent"
    assert events[0]["details"]["status"] == "allowed"
    assert "199" not in serialized
    assert "discount" not in serialized
