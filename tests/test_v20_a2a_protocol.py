from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import TRACE_DIR
from app.orchestration.a2a import (
    A2ABudget,
    A2ABudgetExceededError,
    A2AContractError,
    A2ACoordinator,
    A2AStateTransitionError,
    AgentCapability,
    AgentCard,
    CapabilityDirectory,
)
from app.orchestration.artifacts import ListingArtifact
from app.orchestration.planner import Planner
from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval
from app.tools.browser_tools import reset_seller_center
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，主要面向大学生，"
    "库存800件，毛利率不能低于25%。"
)
NOW = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def records_by_capability(state) -> dict[str, object]:
    return {
        record.request.capability_id: record
        for record in state.a2a_delegations.values()
    }


def test_capability_directory_discovers_each_specialist_deterministically() -> None:
    directory = CapabilityDirectory()

    card, capability = directory.discover(
        "strategy.plan", expected_agent="strategy_agent"
    )

    assert card.agent_name == "strategy_agent"
    assert capability.input_artifact_types == (
        "research_evidence",
        "market_price_assessment",
    )
    assert capability.output_artifact_type == "strategy"
    assert capability.allowed_tools == (
        "suggest_discount",
        "calculate_margin",
        "check_inventory",
        "forecast_demand",
        "query_campaign_history",
        "analyze_competitor_price_trends",
    )


def test_directory_rejects_ambiguous_capability_routes() -> None:
    capability = AgentCapability(
        capability_id="duplicate.capability", output_artifact_type="strategy"
    )

    with pytest.raises(A2AContractError, match="Ambiguous"):
        CapabilityDirectory(
            [
                AgentCard(agent_name="agent_one", capabilities=(capability,)),
                AgentCard(agent_name="agent_two", capabilities=(capability,)),
            ]
        )


def test_advertised_capability_tools_must_match_registry_permissions() -> None:
    directory = CapabilityDirectory(
        [
            AgentCard(
                agent_name="market_agent",
                capabilities=(
                    AgentCapability(
                        capability_id="unsafe.market",
                        output_artifact_type="research_evidence",
                        allowed_tools=("browser_execute",),
                    ),
                ),
            )
        ]
    )

    with pytest.raises(A2AContractError, match="unauthorized tool"):
        directory.validate_tool_registry(ToolRegistry())


def test_planner_assigns_explicit_capabilities_to_every_dag_node() -> None:
    state = Planner().build_initial_state(GOAL)

    assert {
        node_id: node.capability_id for node_id, node in state.nodes.items()
    } == {
        "market": "market.research",
        "market_price_gate": "market.price_assess",
        "listing": "listing.compose",
        "strategy": "strategy.plan",
        "review": "risk.review",
        "browser": "seller.execute",
    }


def test_full_workflow_builds_typed_a2a_artifact_lineage() -> None:
    reset_seller_center()
    state = run_workflow(GOAL, approved=False)
    records = records_by_capability(state)

    assert len(records) == 6
    assert all(record.status == "completed" for record in records.values())
    market_ref = records["market.research"].output_artifact_ref
    gate_ref = records["market.price_assess"].output_artifact_ref
    listing_ref = records["listing.compose"].output_artifact_ref
    strategy_ref = records["strategy.plan"].output_artifact_ref
    review_ref = records["risk.review"].output_artifact_ref
    assert records["market.research"].request.input_artifact_refs == ()
    assert records["market.price_assess"].request.input_artifact_refs == (market_ref,)
    assert records["listing.compose"].request.input_artifact_refs == (market_ref, gate_ref)
    assert records["strategy.plan"].request.input_artifact_refs == (market_ref, gate_ref)
    assert set(records["risk.review"].request.input_artifact_refs) == {
        listing_ref,
        strategy_ref,
    }
    assert records["seller.execute"].request.input_artifact_refs == (review_ref,)
    assert len(state.a2a_events) == 24
    assert all(
        handoff.delegation_id is not None
        and handoff.input_artifact_refs
        == state.a2a_delegations[handoff.delegation_id].request.input_artifact_refs
        for handoff in state.handoffs
    )
    assert all(
        set(handoff.input_artifact_refs).issubset(set(handoff.artifact.evidence_refs))
        for handoff in state.handoffs
        if handoff.artifact is not None
    )


def test_a2a_state_machine_rejects_skipped_transition_and_unknown_actor() -> None:
    state = Planner().build_initial_state(GOAL)
    coordinator = A2ACoordinator(clock=lambda: NOW)
    record = coordinator.create_for_node(state, state.nodes["market"])

    with pytest.raises(A2AStateTransitionError, match="created -> completed"):
        coordinator.transition(
            state,
            record.request.delegation_id,
            "completed",
            actor="market_agent",
        )
    with pytest.raises(A2AContractError, match="not part"):
        coordinator.transition(
            state,
            record.request.delegation_id,
            "accepted",
            actor="listing_agent",
        )


def test_delegation_requires_dependency_artifacts_before_dispatch() -> None:
    state = Planner().build_initial_state(GOAL)

    with pytest.raises(A2AContractError, match="no shared Artifact"):
        A2ACoordinator().create_for_node(state, state.nodes["listing"])


def test_completion_rejects_wrong_artifact_type() -> None:
    state = Planner().build_initial_state(GOAL)
    coordinator = A2ACoordinator(clock=lambda: NOW)
    record = coordinator.create_for_node(state, state.nodes["market"])
    coordinator.transition(
        state, record.request.delegation_id, "accepted", actor="market_agent"
    )
    coordinator.transition(
        state, record.request.delegation_id, "running", actor="market_agent"
    )
    wrong_artifact = ListingArtifact(
        task_id=state.task_id,
        producer="market_agent",
        input_state_version=state.state_version,
        title="这是一个足够长的测试商品标题",
        keywords=("测试",),
        bullets=("测试卖点",),
        compliance_notes=(),
        generation_mode="test",
    )

    with pytest.raises(A2AContractError, match="must produce"):
        coordinator.validate_completion(
            state, state.nodes["market"], wrong_artifact
        )


def test_expired_delegation_cannot_be_accepted() -> None:
    current = [NOW]
    state = Planner().build_initial_state(GOAL)
    coordinator = A2ACoordinator(
        delegation_timeout_seconds=5, clock=lambda: current[0]
    )
    record = coordinator.create_for_node(state, state.nodes["market"])
    current[0] = NOW + timedelta(seconds=6)

    with pytest.raises(A2AContractError, match="expired"):
        coordinator.transition(
            state,
            record.request.delegation_id,
            "accepted",
            actor="market_agent",
        )


def test_agent_card_concurrency_limit_is_enforced() -> None:
    state = Planner().build_initial_state(GOAL)
    coordinator = A2ACoordinator()
    coordinator.create_for_node(state, state.nodes["market"])

    with pytest.raises(A2ABudgetExceededError, match="concurrency"):
        coordinator.create_for_node(state, state.nodes["market"])


def test_global_delegation_and_fanout_budgets_fail_closed() -> None:
    state = Planner().build_initial_state(GOAL)
    state.a2a_budget = A2ABudget(
        max_delegations=1,
        max_delegations_per_agent=1,
        max_hops=1,
        max_fanout=1,
    )
    coordinator = A2ACoordinator()
    coordinator.create_for_node(state, state.nodes["market"])

    with pytest.raises(A2ABudgetExceededError, match="global"):
        coordinator.create_for_node(state, state.nodes["market"])
    with pytest.raises(A2ABudgetExceededError, match="fanout"):
        coordinator.assert_batch_budget(
            state, [state.nodes["listing"], state.nodes["strategy"]]
        )


def test_retry_delegation_links_to_failed_parent_and_increments_attempt() -> None:
    state = Planner().build_initial_state(GOAL)
    coordinator = A2ACoordinator(clock=lambda: NOW)
    first = coordinator.create_for_node(state, state.nodes["market"])
    coordinator.transition(
        state, first.request.delegation_id, "accepted", actor="market_agent"
    )
    coordinator.transition(
        state, first.request.delegation_id, "running", actor="market_agent"
    )
    coordinator.fail_active(state, state.nodes["market"], "injected failure")

    second = coordinator.create_for_node(state, state.nodes["market"])

    assert second.request.parent_delegation_id == first.request.delegation_id
    assert second.request.attempt == 2
    assert second.request.idempotency_key != first.request.idempotency_key


def test_approval_resume_creates_new_browser_delegation_with_parent_link() -> None:
    reset_seller_center()
    initial = run_workflow(GOAL, approved=False)
    first_id = initial.nodes["browser"].delegation_id
    assert first_id is not None

    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(
            approved=True,
            approver="v20-test",
            reason="verify A2A resume lineage",
        ),
        expected_checkpoint_version=initial.checkpoint_version,
    )
    second_id = resumed.nodes["browser"].delegation_id

    assert resumed.status == "completed"
    assert second_id is not None and second_id != first_id
    second = resumed.a2a_delegations[second_id]
    assert second.request.parent_delegation_id == first_id
    assert second.request.attempt == 2
    assert second.status == "completed"
    assert second.output_artifact_ref == resumed.latest_artifacts["browser_agent"]


def test_a2a_trace_contains_envelopes_without_copying_business_payloads() -> None:
    state = run_workflow(GOAL, approved=False)
    trace_path = TRACE_DIR / f"{state.run_id}.jsonl"
    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    a2a_events = [event for event in events if event["event_type"] == "a2a_message"]
    serialized = json.dumps(a2a_events, ensure_ascii=False)

    assert len(a2a_events) == 24
    assert "input_artifact_refs" in serialized
    assert "output_artifact_ref" in serialized
    assert "launch_plan" not in serialized
    assert "bullets" not in serialized
