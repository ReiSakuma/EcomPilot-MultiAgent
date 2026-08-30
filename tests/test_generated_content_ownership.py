from __future__ import annotations

from app.agents.review import ReviewAgent
from app.agents.listing import ListingAgent
from app.agents.supervisor import Supervisor
from app.copilot.compiler import RequestCompiler
from app.copilot.facade import _assistant_message
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.policy import LlmPolicy
from app.model.contracts import CoreReviewOutput, ListingModelOutput
from app.observability.recorder import TraceRecorder
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.failures import TaskOutcome, business_failure
from app.orchestration.handoff import Handoff
from app.orchestration.planner import Planner
from app.safety.content_revision import normalize_listing_semantics
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本95元的入耳式无线耳机，目标售价199元，库存800件，"
    "最低毛利率25%。已确认功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
)


class ListingOutputAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(provider="deepseek", model="deepseek-v4-pro", api_key="fixture")
        self.calls = 0

    def complete(self, prompt, json_schema=None, *, max_output_tokens=None):
        self.calls += 1
        return ModelResponse(
            call_id=f"model_listing_{self.calls}",
            provider=self.provider,
            model=self.model,
            text=(
                '{"title":"入耳式无线游戏耳机","keywords":["无线耳机"],'
                '"bullets":["支持蓝牙5.3","为游戏爱好者设计，性能出色"],'
                '"compliance_notes":["仅采用已确认功能"]}'
            ),
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            usage_source="test",
            prompt_tokens_estimate=100,
            completion_tokens_estimate=50,
        )


class CategoryOnlyListingAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(provider="deepseek", model="deepseek-v4-pro", api_key="fixture")
        self.calls = 0

    def complete(self, prompt, json_schema=None, *, max_output_tokens=None):
        self.calls += 1
        return ModelResponse(
            call_id=f"model_category_title_{self.calls}",
            provider=self.provider,
            model=self.model,
            text=(
                '{"title":"游戏耳机","keywords":["游戏耳机"],'
                '"bullets":["专为游戏玩家设计"],'
                '"compliance_notes":["未包含未经确认的功能宣传"]}'
            ),
            input_tokens=100,
            output_tokens=40,
            total_tokens=140,
            usage_source="test",
            prompt_tokens_estimate=100,
            completion_tokens_estimate=40,
        )


def test_natural_product_name_is_explicit_product_form() -> None:
    state = Planner().build_initial_state(GOAL)
    compiled = RequestCompiler(ModelAdapter(provider="mock")).compile(GOAL)

    assert state.constraints["confirmed_product_form"] == "入耳式"
    assert compiled.structured_request["confirmed_product_form"] == "入耳式"


def test_generated_qualitative_claim_is_replaced_with_confirmed_facts() -> None:
    listing = {
        "title": "入耳式无线游戏耳机",
        "keywords": ["无线耳机"],
        "bullets": ["为游戏爱好者设计，性能出色"],
    }

    corrections = normalize_listing_semantics(
        listing,
        category="无线耳机",
        confirmed_features=["蓝牙5.3", "游戏低延迟", "长续航"],
        confirmed_product_form="入耳式",
    )

    assert all("性能出色" not in item for item in listing["bullets"])
    assert listing["bullets"] == ["支持蓝牙5.3，支持游戏低延迟模式，支持长续航"]
    assert corrections[0]["issue_code"] == "derived_performance_claim"


def test_deepseek_effect_paraphrases_are_projected_to_confirmed_facts() -> None:
    listing = {
        "title": "入耳式无线游戏耳机",
        "keywords": ["无线耳机"],
        "bullets": [
            "蓝牙5.3芯片，连接快速稳定，游戏低延迟，声画同步，畅玩更尽兴。",
            "长续航配合快充，告别电量焦虑。",
            "通话降噪，有效降低环境噪音，通话清晰。",
        ],
    }

    corrections = normalize_listing_semantics(
        listing,
        category="无线耳机",
        confirmed_features=["蓝牙5.3", "游戏低延迟", "长续航", "快充", "通话降噪"],
        confirmed_product_form="入耳式",
    )

    assert listing["bullets"] == [
        "支持蓝牙5.3，支持游戏低延迟模式",
        "支持长续航，支持快充",
        "支持通话降噪",
    ]
    assert {item["issue_code"] for item in corrections} == {
        "confirmed_feature_projection"
    }


def test_punctuation_cleanup_is_not_reported_as_policy_correction() -> None:
    listing = {
        "title": "入耳式无线游戏耳机",
        "keywords": ["无线耳机"],
        "bullets": ["适用于日常使用。"],
    }

    corrections = normalize_listing_semantics(
        listing,
        category="无线耳机",
        confirmed_features=[],
        confirmed_product_form="入耳式",
    )

    assert corrections == []
    assert listing["bullets"] == ["适用于日常使用。"]


def test_listing_agent_cleans_its_own_generated_claim_before_review() -> None:
    state = Planner().build_initial_state(GOAL)
    state.agent_outputs["market_agent"] = {
        "keywords": ["无线耳机"],
        "sample_size": {"competitors": 3, "reviews": 6},
    }
    adapter = ListingOutputAdapter()
    agent = ListingAgent(
        ToolRegistry(),
        model_adapter=adapter,
        llm_policy=LlmPolicy(enabled_agents={"listing_agent"}),
    )

    handoff = agent.run(state)

    assert adapter.calls == 1
    assert all("性能出色" not in item for item in handoff.result["bullets"])
    assert handoff.result["semantic_corrections"]
    assert handoff.result["content_normalization_version"] == "listing-normalization-v1"


def test_complete_four_character_category_title_is_valid_without_model_repair() -> None:
    state = Planner().build_initial_state(
        "先分析游戏耳机市场价格，再帮我上架一款成本95元、售价199元、库存800件的耳机"
    )
    state.constraints["category"] = "游戏耳机"
    state.constraints["target_audience"] = "游戏玩家"
    state.agent_outputs["market_agent"] = {
        "keywords": ["游戏耳机"],
        "sample_size": {"competitors": 3, "reviews": 6},
    }
    adapter = CategoryOnlyListingAdapter()
    agent = ListingAgent(
        ToolRegistry(),
        model_adapter=adapter,
        llm_policy=LlmPolicy(
            enabled_agents={"listing_agent"},
            fallback_mode="fail_closed",
            max_repair_attempts=1,
        ),
    )

    handoff = agent.run(state)

    assert adapter.calls == 1
    assert handoff.result["title"] == "游戏耳机"
    assert state.model_records[0]["structured_validation"] == "passed"
    assert all(
        record["purpose"] != "listing_generation_repair"
        for record in state.model_records
    )


def test_one_character_listing_title_is_repaired_by_semantic_normalization() -> None:
    listing = {
        "title": "耳",
        "keywords": ["游戏耳机"],
        "bullets": ["商品卖点仅采用商家已确认的信息"],
    }

    corrections = normalize_listing_semantics(
        listing,
        category="游戏耳机",
        confirmed_features=[],
        confirmed_product_form=None,
    )

    assert listing["title"] == "游戏耳机 商品上新方案"
    assert any(
        item["issue_code"] == "listing_title_too_short" for item in corrections
    )


def test_listing_protocol_rejects_whitespace_only_title() -> None:
    try:
        ListingModelOutput.model_validate(
            {
                "title": "   ",
                "keywords": ["游戏耳机"],
                "bullets": ["商品卖点仅采用商家已确认的信息"],
                "compliance_notes": [],
            }
        )
    except ValueError as exc:
        assert "title must not be blank" in str(exc)
    else:
        raise AssertionError("whitespace-only title must fail the protocol")


def test_model_review_cannot_turn_subjective_execution_risk_into_hard_stop() -> None:
    generated = CoreReviewOutput.model_validate(
        {
            "issues": [
                {
                    "code": "execution_risk",
                    "field_path": "listing.title",
                    "claim_text": "无线耳机",
                    "message": "产品形态可能不够具体",
                },
                {
                    "code": "unsupported_product_claim",
                    "field_path": "listing.bullets",
                    "claim_text": "性能出色",
                    "message": "概括性性能宣传缺乏依据",
                },
            ]
        }
    )

    findings = ReviewAgent._core_model_findings(
        generated,
        {"title": "无线耳机", "bullets": ["性能出色"]},
        {},
    )

    execution_risk, generated_claim = findings
    assert execution_risk["blocking"] is False
    assert execution_risk["severity"] == "medium"
    assert generated_claim["blocking"] is True
    assert generated_claim["claim_origin"] == "agent_generated"
    assert generated_claim["user_action_required"] is False


def test_first_generated_claim_revision_uses_local_safe_finalize(tmp_path) -> None:
    state = Planner().build_initial_state(GOAL)
    state.run_id = "run_generated_claim_repair"
    executor = WorkflowExecutor(
        {},
        ToolRegistry(),
        TraceRecorder(state.run_id, trace_dir=tmp_path / "traces"),
    )
    finding = {
        "code": "unsupported_product_claim",
        "severity": "high",
        "blocking": True,
        "message": "概括性性能宣传缺乏依据",
        "source_agent": "listing_agent",
        "artifact_type": "listing",
        "field_path": "listing.bullets",
        "claim_text": "性能出色",
        "suggested_action": "remove_unconfirmed_claim",
        "claim_origin": "agent_generated",
        "user_action_required": False,
    }
    handoff = Handoff(
        task_id=state.task_id,
        source_agent="review_agent",
        target_agent="listing_agent",
        status="requires_revision",
        result={
            "revision_requested": True,
            "revision_target": "listing_agent",
            "revision_targets": ["listing_agent"],
            "review_findings": [finding],
        },
    )

    executor._schedule_compliance_revision(state, handoff, "artifact_review")

    loop = state.workflow_loops["compliance_repair"]
    assert loop.iteration == 1
    assert loop.safe_finalize is True


def test_generated_content_failure_does_not_blame_user_input() -> None:
    state = Planner().build_initial_state(GOAL)
    state.agent_outputs["review_agent"] = {
        "review_findings": [
            {
                "code": "unsupported_product_claim",
                "blocking": True,
                "claim_origin": "agent_generated",
                "user_action_required": False,
            }
        ]
    }
    state.record_failure(
        business_failure(
            code="llm_review:unsupported_product_claim",
            stage="review",
            user_message="性能出色缺乏依据。",
            developer_message="generated listing claim",
        )
    )

    message = _assistant_message(state, TaskOutcome.business_rejected)

    assert "你的原始业务条件无需调整" in message
    assert "请调整条件" not in message


def test_approval_message_explains_market_evidence_before_sync_prompt() -> None:
    state = Planner().build_initial_state(GOAL)
    state.agent_outputs["market_agent"] = {
        "price_band": [199, 229],
        "median_price": 219,
        "sample_size": {"competitors": 3, "excluded_competitors": 2},
        "sql_research": {
            "insight_summary": "199元位于核心价格带下沿，适合强调价格竞争力。"
        },
    }
    state.agent_outputs["market_price_gate_agent"] = {
        "position": "within_market",
        "target_price": 199,
        "core_reference_price": 219,
        "core_price_band": [199, 229],
        "deviation_rate": -0.091324,
        "core_sample_count": 3,
        "excluded_sample_count": 2,
        "evidence_quality": "low",
    }
    state.agent_outputs["listing_agent"] = {"title": "入耳式无线游戏耳机"}
    state.agent_outputs["strategy_agent"] = {
        "price": 199,
        "margin": {"margin_rate": 0.4693},
    }

    message = _assistant_message(state, TaskOutcome.awaiting_approval)

    assert message.index("市场价格分析") < message.index("方案尚未修改店铺")
    assert "199 至 229 元" in message
    assert "相对参考价偏差 -9.13%" in message
    assert "3 个核心可比样本" in message
    assert "证据质量较低" in message
    assert "Market Agent 的补充判断" in message
    assert message.endswith("请核对后确认执行。")


def test_tool_records_can_be_filtered_by_task() -> None:
    tools = ToolRegistry()
    with tools.agent_scope("market_agent", task_id="task_a"):
        tools.call("build_market_report", category="无线耳机")
    with tools.agent_scope("market_agent", task_id="task_b"):
        tools.call("build_market_report", category="无线耳机")

    assert len(tools.records()) == 2
    assert [item.task_id for item in tools.records("task_a")] == ["task_a"]
    assert [item.task_id for item in tools.records("task_b")] == ["task_b"]


def test_reused_supervisor_does_not_leak_tool_records_between_tasks() -> None:
    supervisor = Supervisor()

    first = supervisor.run(GOAL, approved=False)
    second = supervisor.run(GOAL, approved=False)

    assert first.status == second.status == "waiting_for_approval"
    assert first.tool_records
    assert second.tool_records
    assert all(item["task_id"] == first.task_id for item in first.tool_records)
    assert all(item["task_id"] == second.task_id for item in second.tool_records)
    assert len(first.tool_records) == len(second.tool_records)
