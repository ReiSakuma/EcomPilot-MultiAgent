from __future__ import annotations

from app.agents.listing import ListingAgent
from app.agents.strategy import StrategyAgent
from app.model.adapter import ModelAdapter
from app.model.policy import LlmPolicy
from app.orchestration.planner import Planner
from app.orchestration.state import WorkflowLoopState
from app.tools.registry import ToolRegistry


class NoCallAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(provider="deepseek", model="deepseek-v4-pro", api_key="fixture")
        self.calls = 0

    def complete(self, prompt, json_schema=None, *, max_output_tokens=None):
        self.calls += 1
        raise AssertionError("safe finalization must not call the model")


def repair_state(target_agent: str):
    state = Planner().build_initial_state(
        "我要上架一款成本90元、售价200元、库存800件的无线耳机，"
        "毛利率不能低于25%。已确认的产品功能：蓝牙5.3、游戏低延迟。"
    )
    state.agent_outputs["market_agent"] = {
        "keywords": ["无线耳机", "游戏耳机"],
        "sample_size": {"competitors": 1, "reviews": 1},
    }
    finding = {
        "code": "unsupported_product_claim",
        "severity": "high",
        "blocking": True,
        "message": "头戴式电竞耳机的产品形态未经确认",
        "source_agent": target_agent,
        "artifact_type": "listing" if target_agent == "listing_agent" else "strategy",
        "field_path": (
            "listing.title"
            if target_agent == "listing_agent"
            else "strategy.launch_plan"
        ),
        "claim_text": "头戴式电竞耳机",
        "suggested_action": "remove_unconfirmed_claim",
    }
    state.workflow_loops["compliance_repair"] = WorkflowLoopState(
        iteration=2,
        max_iterations=2,
        phase="revision_pending",
        feedback=[finding],
        target_agents=[target_agent],
        safe_finalize=True,
    )
    return state


def test_listing_safe_finalize_removes_claim_without_model_call() -> None:
    state = repair_state("listing_agent")
    state.agent_outputs["listing_agent"] = {
        "title": "高性价比头戴式电竞耳机",
        "keywords": ["头戴式电竞耳机"],
        "bullets": ["头戴式电竞耳机，适合游戏场景"],
        "compliance_notes": ["需要移除未经确认的产品形态"],
        "generation_mode": "llm_revision",
        "revision_iteration": 1,
        "revision_applied_findings": [],
    }
    adapter = NoCallAdapter()
    agent = ListingAgent(
        ToolRegistry(),
        model_adapter=adapter,
        llm_policy=LlmPolicy(enabled_agents={"listing_agent"}),
    )

    handoff = agent.run(state)

    assert adapter.calls == 0
    assert "头戴式电竞耳机" not in handoff.result["title"]
    assert all("头戴式电竞耳机" not in item for item in handoff.result["bullets"])
    assert handoff.result["generation_mode"] == "safe_revision"


def test_strategy_safe_finalize_preserves_verified_numbers_without_model_or_tools() -> None:
    state = repair_state("strategy_agent")
    state.agent_outputs["strategy_agent"] = {
        "price": 200,
        "coupon": 20,
        "launch_plan": "使用20元优惠券，宣传高性价比头戴式电竞耳机。",
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
        "strategy_rationale": "verified fixture",
        "generation_mode": "react",
        "market_price_reference": {},
        "selected_evidence_tools": [],
        "decision_evidence": {},
    }
    adapter = NoCallAdapter()
    tools = ToolRegistry()
    agent = StrategyAgent(
        tools,
        model_adapter=adapter,
        llm_policy=LlmPolicy(
            enabled_agents={"strategy_agent"},
            react_enabled_agents={"strategy_agent"},
        ),
    )

    handoff = agent.run(state)

    assert adapter.calls == 0
    assert tools.records() == []
    assert "头戴式电竞耳机" not in handoff.result["launch_plan"]
    assert handoff.result["margin"]["margin_rate"] == 0.5
    assert handoff.result["inventory_check"]["remaining"] == 500
    assert handoff.result["generation_mode"] == "safe_revision"
    assert "已确认卖点" not in handoff.result["launch_plan"]


def test_strategy_safe_finalize_rewrites_percentage_discount_as_verified_amount() -> None:
    state = repair_state("strategy_agent")
    finding = {
        "code": "discount_representation_mismatch",
        "severity": "high",
        "blocking": True,
        "message": "10元优惠被写成了百分比",
        "source_agent": "strategy_agent",
        "artifact_type": "strategy",
        "field_path": "strategy.launch_plan",
        "claim_text": "折扣10%",
        "suggested_action": "fix_discount_representation",
    }
    state.workflow_loops["compliance_repair"].feedback = [finding]
    state.agent_outputs["strategy_agent"] = {
        "price": 230,
        "coupon": 10,
        "launch_plan": (
            "首销折扣10%（净价220元），折扣保留至10%以内，"
            "并留出20%上限内的后续让利空间。"
        ),
        "planned_units": 300,
        "margin": {
            "price": 230,
            "discount": 10,
            "net_price": 220,
            "cost": 95,
            "margin": 125,
            "margin_rate": 0.5682,
        },
        "inventory_check": {
            "inventory": 800,
            "planned_units": 300,
            "valid": True,
            "remaining": 500,
        },
        "strategy_rationale": "verified fixture",
        "generation_mode": "react",
        "market_price_reference": {},
        "selected_evidence_tools": [],
        "decision_evidence": {},
    }
    adapter = NoCallAdapter()
    agent = StrategyAgent(
        ToolRegistry(),
        model_adapter=adapter,
        llm_policy=LlmPolicy(
            enabled_agents={"strategy_agent"},
            react_enabled_agents={"strategy_agent"},
        ),
    )

    handoff = agent.run(state)

    assert adapter.calls == 0
    assert "折扣10%" not in handoff.result["launch_plan"]
    assert "10元优惠券" in handoff.result["launch_plan"]
    assert "标价200元" in handoff.result["launch_plan"]
    assert "到手价190元" in handoff.result["launch_plan"]
    assert handoff.result["coupon"] == 10
    assert handoff.result["margin"]["margin_rate"] == 0.5263
