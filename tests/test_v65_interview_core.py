from __future__ import annotations

from app.config import PROJECT_VERSION, PROMPT_VERSION
from app.linked_runtime import REQUIRED_LLM_AGENTS, REQUIRED_REACT_AGENTS
from app.model.contracts import CoreReviewOutput, CoreStrategyProposal
from app.model.policy import LlmPolicy
from app.orchestration.workflow import run_workflow
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，主要面向大学生，"
    "库存800件，毛利率不能低于25%。已确认功能：蓝牙5.3、游戏低延迟。"
    "已确认产品形态：入耳式。"
)


def test_v65_identity_and_live_core_are_small() -> None:
    assert PROJECT_VERSION == "0.65.0"
    assert PROMPT_VERSION == "interview-core-v1"
    assert REQUIRED_LLM_AGENTS == {
        "listing_agent",
        "strategy_agent",
        "review_agent",
    }
    assert REQUIRED_REACT_AGENTS == {"strategy_agent"}


def test_v65_strategy_and_review_contracts_are_bounded() -> None:
    strategy_schema = CoreStrategyProposal.model_json_schema()
    review_schema = CoreReviewOutput.model_json_schema()
    assert set(strategy_schema["properties"]) == {
        "launch_plan",
        "rationale",
        "discount_amount_yuan",
    }
    assert set(review_schema["properties"]) == {"issues"}
    assert review_schema["properties"]["issues"]["maxItems"] == 3


def test_v65_retires_candidate_tools_and_policy_switches() -> None:
    specs = ToolRegistry().specs()
    assert "simulate_discount_scenarios" not in specs
    assert "evaluate_strategy_candidates" not in specs
    policy = LlmPolicy(enabled_agents=set())
    assert not hasattr(policy, "strategy_candidate_pipeline")
    assert policy.react_max_steps == 2
    assert policy.react_max_tool_calls == 2


def test_v65_normal_listing_reaches_human_approval_with_one_strategy() -> None:
    state = run_workflow(GOAL, approved=False)
    assert state.status == "waiting_for_approval"
    assert not state.failure_history
    strategy = state.agent_outputs["strategy_agent"]
    assert strategy["core_protocol_version"] == "interview-core-strategy-v1"
    assert "candidate_protocol_version" not in strategy
    assert "candidate_proposals" not in strategy
    assert len(strategy["selected_evidence_tools"]) <= 1
    assert state.agent_outputs["review_agent"]["approved_for_execution"] is True


def test_v65_approved_listing_keeps_idempotent_execution_path() -> None:
    state = run_workflow(GOAL, approved=True)
    assert state.status == "completed"
    assert state.agent_outputs["browser_agent"]["verification"]["verified"] is True
