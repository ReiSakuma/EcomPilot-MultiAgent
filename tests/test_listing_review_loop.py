from __future__ import annotations

import json

from app.model.adapter import ModelAdapter
from app.agents.market_price_gate import MarketPriceGateAgent
from app.observability.recorder import TraceRecorder
from app.orchestration.a2a import A2ACoordinator
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.handoff import Handoff
from app.orchestration.planner import Planner
from app.orchestration.state import WorkflowLoopState
from app.tools.product_tools import build_market_report
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本90元、售价200元、库存800件的无线耳机，"
    "主要面向游戏爱好者，毛利率不能低于25%。"
)


def test_planner_extracts_confirmed_product_features() -> None:
    state = Planner().build_initial_state(
        GOAL
        + "已确认的产品功能：蓝牙5.3、游戏低延迟、长续航。"
        + "运营目标：面向游戏用户。"
    )

    assert state.constraints["confirmed_features"] == [
        "蓝牙5.3",
        "游戏低延迟",
        "长续航",
    ]


def test_planner_extracts_confirmed_product_form_separately_from_goal() -> None:
    state = Planner().build_initial_state(
        GOAL
        + "已确认的产品功能：蓝牙5.3、游戏低延迟。"
        + "已确认的产品形态：头戴式。"
        + "运营目标：面向电竞用户。"
    )

    assert state.constraints["confirmed_product_form"] == "头戴式"


def test_planner_accepts_product_form_used_as_a_natural_noun_modifier() -> None:
    state = Planner().build_initial_state(
        "我要上架一款成本95元、售价199元、库存800件的入耳式无线耳机。"
    )

    assert state.constraints["confirmed_product_form"] == "入耳式"


def test_repeat_fingerprint_distinguishes_bounded_review_iterations(tmp_path) -> None:
    state = Planner().build_initial_state(GOAL)
    state.run_id = "run_repeat_fingerprint"
    executor = WorkflowExecutor(
        {},
        ToolRegistry(),
        TraceRecorder(state.run_id, trace_dir=tmp_path / "traces"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoints"),
    )
    review = state.nodes["review"]
    state.workflow_loops["compliance_repair"] = loop = WorkflowLoopState(
        iteration=1,
        phase="review_pending",
        finding_fingerprint="finding-a",
        target_agents=["listing_agent"],
        completed_agents=["listing_agent"],
        revised_artifact_refs={"listing_agent": "artifact-revision-1"},
    )
    first = executor._ready_node_repeat_payload(state, [review])

    loop.iteration = 2
    loop.revised_artifact_refs = {"listing_agent": "artifact-revision-2"}
    second = executor._ready_node_repeat_payload(state, [review])

    assert first != second
    assert first["nodes"] == second["nodes"] == ["review"]


class FakeAgent:
    def __init__(self, callback):
        self.callback = callback
        self.calls = 0
        self.model_adapter = ModelAdapter()

    def run(self, state):
        self.calls += 1
        return self.callback(state, self.calls)


def market_handoff(state, _calls):
    result = build_market_report("无线耳机", "游戏爱好者")
    result["research_mode"] = "fixture"
    result["sql_research"] = None
    return Handoff(
        task_id=state.task_id,
        source_agent="market_agent",
        target_agent="listing_agent",
        result=result,
    )


def listing_handoff(state, calls):
    loop = state.workflow_loops.get("compliance_repair")
    revising = bool(loop)
    return Handoff(
        task_id=state.task_id,
        source_agent="listing_agent",
        target_agent="review_agent",
        result={
            "title": "修订后的无线游戏耳机" if revising else "含未经确认功能的无线游戏耳机",
            "keywords": ["无线耳机", "游戏耳机"],
            "bullets": ["蓝牙连接"],
            "compliance_notes": ["仅使用已确认功能"],
            "generation_mode": "fixture_revision" if revising else "fixture",
            "revision_iteration": 1 if revising else 0,
            "revision_applied_findings": (
                list(loop.feedback)
                if revising
                else []
            ),
            "semantic_corrections": (
                [
                    {
                        "correction_id": "correction_workflow_trace",
                        "source_agent": "listing_agent",
                        "field_path": "listing.title",
                        "issue_code": "unsupported_product_claim",
                        "before": "含未经确认功能的无线游戏耳机",
                        "after": "修订后的无线游戏耳机",
                        "reason": "workflow trace fixture",
                        "evidence_refs": ["task.constraints.confirmed_features"],
                        "method": "deterministic_semantic_guardrail",
                        "status": "corrected",
                    }
                ]
                if revising
                else []
            ),
        },
    )


def strategy_handoff(state, _calls):
    return Handoff(
        task_id=state.task_id,
        source_agent="strategy_agent",
        target_agent="review_agent",
        result={
            "price": 200,
            "coupon": 20,
            "launch_plan": "使用20元优惠券",
            "planned_units": 300,
            "margin": {
                "price": 200,
                "discount": 20,
                "net_price": 180,
                "cost": 90,
                "margin": 90,
                "margin_rate": 0.5,
            },
            "inventory_check": {
                "inventory": 800,
                "planned_units": 300,
                "valid": True,
                "remaining": 500,
            },
            "strategy_rationale": "fixture",
            "generation_mode": "fixture",
            "market_price_reference": {},
            "selected_evidence_tools": [],
            "decision_evidence": {},
        },
    )


def review_handoff(state, calls):
    needs_revision = calls == 1
    findings = (
        [
            {
                "code": "unsupported_product_claim",
                "severity": "high",
                "blocking": True,
                "message": "删除未经确认的产品功能",
                "source_agent": "listing_agent",
                "artifact_type": "listing",
                "field_path": "listing.title",
                "claim_text": "未经确认功能",
                "suggested_action": "remove_unconfirmed_claim",
            }
        ]
        if needs_revision
        else [
            {
                "code": "no_blocking_issue",
                "severity": "low",
                "blocking": False,
                "message": "修订后未发现阻断问题",
            }
        ]
    )
    return Handoff(
        task_id=state.task_id,
        source_agent="review_agent",
        target_agent="listing_agent" if needs_revision else "browser_agent",
        status="requires_revision" if needs_revision else "completed",
        result={
            "approved_for_execution": not needs_revision,
            "violations": (
                ["llm_review:unsupported_product_claim"]
                if needs_revision
                else []
            ),
            "review_notes": [findings[0]["message"]],
            "review_findings": findings,
            "revision_requested": needs_revision,
            "revision_target": "listing_agent" if needs_revision else None,
            **({"revision_targets": ["listing_agent"]} if needs_revision else {}),
            "generation_mode": "fixture",
            "execution_plan": {
                "operation": "update_listing",
                "product_id": "wireless_earbud_draft",
                "title": state.agent_outputs["listing_agent"]["title"],
                "bullets": state.agent_outputs["listing_agent"]["bullets"],
                "price": 200,
                "stock": 800,
                "coupon": 20,
            },
        },
    )


def browser_handoff(state, _calls):
    return Handoff(
        task_id=state.task_id,
        source_agent="browser_agent",
        target_agent="supervisor",
        status="requires_review",
        result={
            "risk": "high",
            "execution_plan": state.agent_outputs["review_agent"]["execution_plan"],
        },
    )


def test_listing_review_loop_revises_once_and_preserves_a2a_lineage(tmp_path) -> None:
    agents = {
        "market_agent": FakeAgent(market_handoff),
        "market_price_gate_agent": MarketPriceGateAgent(ToolRegistry()),
        "listing_agent": FakeAgent(listing_handoff),
        "strategy_agent": FakeAgent(strategy_handoff),
        "review_agent": FakeAgent(review_handoff),
        "browser_agent": FakeAgent(browser_handoff),
    }
    state = Planner().build_initial_state(GOAL)
    state.run_id = "run_listing_revision"
    executor = WorkflowExecutor(
        agents,
        ToolRegistry(),
        TraceRecorder(state.run_id, trace_dir=tmp_path / "traces"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoints"),
        a2a_coordinator=A2ACoordinator(),
    )

    result = executor.run(state)

    assert result.status == "waiting_for_approval"
    assert agents["listing_agent"].calls == 2
    assert agents["review_agent"].calls == 2
    loop = result.workflow_loops["compliance_repair"]
    assert loop.iteration == 1
    assert loop.phase == "completed"
    assert result.agent_outputs["listing_agent"]["revision_iteration"] == 1
    revision_record = next(
        record
        for record in result.a2a_delegations.values()
        if record.request.capability_id == "listing.revise"
    )
    input_types = {
        result.artifacts[artifact_id].artifact_type
        for artifact_id in revision_record.request.input_artifact_refs
    }
    assert input_types == {
        "research_evidence",
        "market_price_assessment",
        "risk_decision",
    }
    trace_events = [
        json.loads(line)
        for line in (tmp_path / "traces" / "run_listing_revision.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    correction_event = next(
        event
        for event in trace_events
        if event["event_type"] == "semantic_correction"
    )
    assert correction_event["component_name"] == "listing_agent"
    assert correction_event["details"]["before"] != correction_event["details"]["after"]


def strategy_revision_handoff(state, calls):
    result = strategy_handoff(state, calls).result
    loop = state.workflow_loops.get("compliance_repair")
    if loop and "strategy_agent" in loop.target_agents:
        result["launch_plan"] = "使用20元优惠券，围绕已确认的游戏卖点进行冷启动。"
        result["generation_mode"] = "fixture_revision"
        result["revision_iteration"] = loop.iteration
        result["revision_applied_findings"] = list(loop.feedback)
    else:
        result["launch_plan"] = "使用20元优惠券，宣传高性价比头戴式电竞耳机。"
    return Handoff(
        task_id=state.task_id,
        source_agent="strategy_agent",
        target_agent="review_agent",
        result=result,
    )


def strategy_review_handoff(state, calls):
    needs_revision = calls == 1
    findings = (
        [
            {
                "code": "unsupported_product_claim",
                "severity": "high",
                "blocking": True,
                "message": "头戴式电竞耳机的产品形态未经确认",
                "source_agent": "strategy_agent",
                "artifact_type": "strategy",
                "field_path": "strategy.launch_plan",
                "claim_text": "头戴式电竞耳机",
                "suggested_action": "remove_unconfirmed_claim",
            }
        ]
        if needs_revision
        else [
            {
                "code": "no_blocking_issue",
                "severity": "low",
                "blocking": False,
                "message": "策略修订后未发现阻断问题",
            }
        ]
    )
    result = {
        "approved_for_execution": not needs_revision,
        "violations": (
            ["llm_review:unsupported_product_claim"] if needs_revision else []
        ),
        "review_notes": [findings[0]["message"]],
        "review_findings": findings,
        "revision_requested": needs_revision,
        "revision_target": "strategy_agent" if needs_revision else None,
        "generation_mode": "fixture",
        "execution_plan": {
            "operation": "update_listing",
            "product_id": "wireless_earbud_draft",
            "title": state.agent_outputs["listing_agent"]["title"],
            "bullets": state.agent_outputs["listing_agent"]["bullets"],
            "price": 200,
            "stock": 800,
            "coupon": 20,
        },
    }
    if needs_revision:
        result["revision_targets"] = ["strategy_agent"]
    return Handoff(
        task_id=state.task_id,
        source_agent="review_agent",
        target_agent="strategy_agent" if needs_revision else "browser_agent",
        status="requires_revision" if needs_revision else "completed",
        result=result,
    )


def test_strategy_review_loop_routes_revision_to_strategy_only(tmp_path) -> None:
    agents = {
        "market_agent": FakeAgent(market_handoff),
        "market_price_gate_agent": MarketPriceGateAgent(ToolRegistry()),
        "listing_agent": FakeAgent(listing_handoff),
        "strategy_agent": FakeAgent(strategy_revision_handoff),
        "review_agent": FakeAgent(strategy_review_handoff),
        "browser_agent": FakeAgent(browser_handoff),
    }
    state = Planner().build_initial_state(GOAL)
    state.run_id = "run_strategy_revision"
    executor = WorkflowExecutor(
        agents,
        ToolRegistry(),
        TraceRecorder(state.run_id, trace_dir=tmp_path / "traces"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoints"),
        a2a_coordinator=A2ACoordinator(),
    )

    result = executor.run(state)

    assert result.status == "waiting_for_approval"
    assert agents["listing_agent"].calls == 1
    assert agents["strategy_agent"].calls == 2
    assert agents["review_agent"].calls == 2
    assert "头戴式电竞耳机" not in result.agent_outputs["strategy_agent"][
        "launch_plan"
    ]
    loop = result.workflow_loops["compliance_repair"]
    assert loop.target_agents == ["strategy_agent"]
    assert loop.completed_agents == ["strategy_agent"]
    assert loop.phase == "completed"
    revision_record = next(
        record
        for record in result.a2a_delegations.values()
        if record.request.capability_id == "strategy.revise"
    )
    input_types = {
        result.artifacts[artifact_id].artifact_type
        for artifact_id in revision_record.request.input_artifact_refs
    }
    assert input_types == {
        "research_evidence",
        "market_price_assessment",
        "risk_decision",
    }
