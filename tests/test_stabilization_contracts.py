from __future__ import annotations

from app.orchestration.failures import (
    AgentOutputContractError,
    TaskOutcome,
    business_failure,
    failure_from_exception,
)
from app.orchestration.planner import Planner
from app.presentation import build_task_presentation, state_response


GOAL = (
    "我要上架一款成本95元、目标售价199元、库存800件的无线耳机，"
    "主要面向大学生，毛利率不能低于25%。"
)


def test_failure_classifier_exposes_stable_user_and_developer_messages() -> None:
    failure = failure_from_exception(
        RuntimeError("Market Agent may issue only one SQL query per ReAct step"),
        stage="market",
        agent_name="market_agent",
        trace_refs=("run_fixture",),
    )

    assert failure.code == "model_protocol_mismatch"
    assert failure.category == "model_protocol"
    assert "工具调用" in failure.user_message
    assert "only one SQL" in failure.developer_message
    assert failure.trace_refs == ("run_fixture",)


def test_task_state_requires_declared_upstream_contract() -> None:
    state = Planner().build_initial_state(GOAL)

    try:
        state.require_agent_output("market_agent", required_keys=("price_band",))
    except AgentOutputContractError as exc:
        failure = failure_from_exception(
            exc, stage="listing", agent_name="listing_agent"
        )
    else:
        raise AssertionError("missing upstream output must fail the contract")

    assert failure.category == "workflow_contract"
    assert failure.code == "workflow_contract_failed"


def test_presentation_uses_business_outcome_without_ui_inference() -> None:
    state = Planner().build_initial_state(GOAL)
    state.status = "failed"
    state.record_failure(
        business_failure(
            code="margin_below_minimum",
            stage="review",
            user_message="预计毛利率低于你的最低要求。",
            developer_message="margin_below_minimum",
        )
    )

    view = build_task_presentation(state)
    payload = state_response(state)

    assert state.outcome is TaskOutcome.business_rejected
    assert view.outcome is TaskOutcome.business_rejected
    assert view.failure.code == "margin_below_minimum"
    assert payload["presentation"]["outcome"] == "business_rejected"
    assert payload["presentation"]["store_modified"] is False


def test_optional_degradation_does_not_become_terminal_failure() -> None:
    state = Planner().build_initial_state(GOAL)
    degradation = failure_from_exception(
        RuntimeError("ReAct step limit exhausted: 2"),
        stage="market_enrichment",
        agent_name="market_agent",
    )
    state.degradations.append(degradation)
    state.status = "waiting_for_approval"
    state.outcome = TaskOutcome.awaiting_approval

    view = build_task_presentation(state)

    assert view.outcome is TaskOutcome.awaiting_approval
    assert view.failure is None
    assert view.degradations[0].code == "model_protocol_mismatch"
