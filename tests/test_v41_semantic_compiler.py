from __future__ import annotations

import json

from app.copilot.compiler import RequestCompiler
from app.copilot.intents import IntentName, RequestMode
from app.model.adapter import ModelIncompleteError, ModelResponse


class SemanticAdapter:
    provider = "deepseek"
    model = "deepseek-v4-pro-test"

    def __init__(self, outputs: list[dict | str | Exception]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def complete(self, prompt: str, json_schema=None, *, max_output_tokens=None):
        self.calls += 1
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return ModelResponse(
            call_id=f"model_semantic_{self.calls}",
            provider=self.provider,
            model=self.model,
            text=text,
            input_tokens=120,
            output_tokens=80,
            total_tokens=200,
            usage_source="test",
            prompt_tokens_estimate=120,
            completion_tokens_estimate=80,
        )


def _proposal(*, fields: list[dict], intent: str = "create_listing") -> dict:
    return {
        "intent": intent,
        "confidence": 0.97,
        "category": None,
        "target_audience": None,
        "fields": fields,
        "explicitly_unknown_fields": [],
        "time_range_days": None,
        "topics": [],
        "unverified_requested_claims": [],
        "prompt_injection_detected": False,
        "rationale": "模型识别到商品上架请求。",
    }


def _field(name: str, value, quote: str, extraction: str = "user_explicit") -> dict:
    return {
        "field_name": name,
        "value": value,
        "source_quote": quote,
        "confidence": 0.96,
        "extraction": extraction,
        "normalization_note": "修正口语或错别字",
    }


def test_llm_first_compiler_recovers_typoed_business_fields() -> None:
    message = "我要上架无线耳机，成夲95园，售介300园，库洊800件，最低毛莉率40%。"
    adapter = SemanticAdapter(
        [_proposal(fields=[
            _field("category", "无线耳机", "上架无线耳机"),
            _field("cost", 95, "成夲95园"),
            _field("target_price", 300, "售介300园"),
            _field("inventory", 800, "库洊800件"),
            _field("min_margin_rate", 0.4, "最低毛莉率40%"),
        ])]
    )

    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]

    assert adapter.calls == 1
    assert compiled.decision.intent is IntentName.create_listing
    assert compiled.assessment.mode is RequestMode.execute
    assert compiled.semantic_status == "model_validated"
    assert compiled.structured_request["cost"] == 95
    assert compiled.structured_request["target_price"] == 300
    assert compiled.structured_request["inventory"] == 800
    assert compiled.structured_request["min_margin_rate"] == 0.4
    assert {
        item.field_name for item in compiled.assessment.field_evidence
        if item.source == "model_extracted"
    } >= {"cost", "target_price", "inventory", "min_margin_rate"}


def test_model_field_with_fabricated_source_quote_is_rejected() -> None:
    adapter = SemanticAdapter([_proposal(fields=[
        _field("category", "无线耳机", "无线耳机"),
        _field("cost", 95, "用户说成本95元"),
    ])])

    compiled = RequestCompiler(adapter).compile("帮我上架无线耳机。")  # type: ignore[arg-type]

    assert compiled.structured_request["cost"] is None
    assert "cost" in compiled.assessment.missing_fields
    assert any(
        item.field_name == "cost" and item.code == "source_not_grounded"
        for item in compiled.semantic_diagnostics
    )


def test_model_may_not_infer_price_inventory_or_confirmed_features() -> None:
    message = "帮我上架一款支持蓝牙5.3的无线耳机，价格和库存请你估计。"
    adapter = SemanticAdapter([_proposal(fields=[
        _field("category", "无线耳机", "无线耳机", "model_inferred"),
        _field("target_price", 299, "价格和库存请你估计", "model_inferred"),
        _field("inventory", 100, "价格和库存请你估计", "model_inferred"),
        _field("confirmed_features", ["蓝牙5.3"], "支持蓝牙5.3", "model_inferred"),
    ])])

    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]

    assert compiled.structured_request["target_price"] is None
    assert compiled.structured_request["inventory"] is None
    assert compiled.structured_request["confirmed_features"] == []
    assert sum(
        item.code == "unsafe_inference" for item in compiled.semantic_diagnostics
    ) == 3


def test_deterministic_margin_policy_runs_after_semantic_compilation() -> None:
    message = "我要上架无线耳机，成夲230园，售介250园，库洊800件，最低毛莉率30%。"
    adapter = SemanticAdapter([_proposal(fields=[
        _field("category", "无线耳机", "无线耳机"),
        _field("cost", 230, "成夲230园"),
        _field("target_price", 250, "售介250园"),
        _field("inventory", 800, "库洊800件"),
        _field("min_margin_rate", 0.3, "最低毛莉率30%"),
    ])])

    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]

    assert adapter.calls == 1
    assert compiled.assessment.mode is RequestMode.clarify
    assert compiled.assessment.preflight_issues[0].code == "margin_infeasible"
    assert compiled.assessment.allowed_scopes == []


def test_invalid_json_receives_one_controlled_schema_repair() -> None:
    message = "我要上架无线耳机，成夲95园，售介300园，库洊800件。"
    valid = _proposal(fields=[
        _field("category", "无线耳机", "无线耳机"),
        _field("cost", 95, "成夲95园"),
        _field("target_price", 300, "售介300园"),
        _field("inventory", 800, "库洊800件"),
    ])
    adapter = SemanticAdapter(["not-json", valid])

    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]

    assert adapter.calls == 2
    assert compiled.semantic_status == "repair_validated"
    assert any(item.code == "schema_repaired" for item in compiled.semantic_diagnostics)
    assert [record["status"] for record in compiled.compiler_model_records] == [
        "invalid_output", "completed"
    ]


def test_model_failure_degrades_to_safe_deterministic_extraction() -> None:
    adapter = SemanticAdapter([RuntimeError("temporary model outage")])
    compiled = RequestCompiler(adapter).compile(  # type: ignore[arg-type]
        "我要上架无线耳机，成本95元，售价300元，库存800件，毛利率不低于40%。"
    )

    assert compiled.assessment.mode is RequestMode.execute
    assert compiled.semantic_status == "deterministic_fallback"
    assert any(item.code == "model_failure" for item in compiled.semantic_diagnostics)
    assert compiled.structured_request["cost"] == 95


def test_length_truncation_gets_one_compact_semantic_retry() -> None:
    message = "帮我上架一个无线而机，进货95块，想卖300，库村800个，毛利最少四成。"
    adapter = SemanticAdapter([
        ModelIncompleteError("DeepSeek response incomplete: finish_reason=length"),
        _proposal(fields=[
            _field("category", "无线耳机", "无线而机"),
            _field("cost", 95, "进货95块"),
            _field("target_price", 300, "想卖300"),
            _field("inventory", 800, "库村800个"),
            _field("min_margin_rate", 0.4, "毛利最少四成"),
        ]),
    ])

    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]

    assert adapter.calls == 2
    assert compiled.assessment.mode is RequestMode.execute
    assert compiled.semantic_status == "model_validated"
    assert [item["purpose"] for item in compiled.compiler_model_records] == [
        "semantic_compilation",
        "semantic_length_retry",
    ]
    assert any(
        item.code == "length_retried" for item in compiled.semantic_diagnostics
    )


def test_colloquial_typo_fallback_keeps_explicit_business_values() -> None:
    message = (
        "帮我上架一个无线而机，进货95块，想卖300，库村800个，"
        "毛利最少四成。功能有蓝牙5.3、游戏低延时、长续航和快充，"
        "主要卖给游戏玩家。"
    )
    adapter = SemanticAdapter([
        RuntimeError("temporary model outage"),
    ])

    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]

    assert compiled.assessment.mode is RequestMode.execute
    assert compiled.semantic_status == "deterministic_fallback"
    assert compiled.structured_request["category"] == "无线耳机"
    assert compiled.structured_request["cost"] == 95
    assert compiled.structured_request["target_price"] == 300
    assert compiled.structured_request["inventory"] == 800
    assert compiled.structured_request["min_margin_rate"] == 0.4
    assert compiled.structured_request["target_audience"] == "游戏玩家"
    assert compiled.structured_request["confirmed_features"] == [
        "蓝牙5.3",
        "游戏低延迟",
        "长续航",
        "快充",
    ]


def test_modify_misclassification_falls_back_to_listing_preflight() -> None:
    message = "耳机成本95元，售价199元，后面改成售价299元；库存800件，但只能投入1000件。"
    adapter = SemanticAdapter([_proposal(
        intent="modify_listing",
        fields=[
            _field("category", "无线耳机", "耳机"),
            _field("cost", 95, "成本95元"),
            _field("target_price", 199, "售价199元"),
            _field("inventory", 800, "库存800件"),
        ],
    )])

    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]

    assert compiled.decision.intent is IntentName.clarify
    assert compiled.decision.original_intent is IntentName.create_listing
    assert compiled.assessment.mode is RequestMode.clarify
    issue = next(
        item
        for item in compiled.assessment.preflight_issues
        if item.code == "conflicting_business_fields"
    )
    assert "target_price=199,299" in issue.evidence
    assert "planned_units=1000>inventory=800" in issue.evidence
    assert "计划投入量超过可用库存" in (
        compiled.assessment.clarification_question or ""
    )


def test_feature_and_form_typos_are_canonicalized_without_becoming_claims() -> None:
    compiled = RequestCompiler(
        SemanticAdapter([RuntimeError("temporary model outage")])
    ).compile(
        "帮我上架无线而机，进货95块，想卖199，库村800个，毛利最少四成。"
        "功能有蓝牙5.3、游戏低延时、长续航和快冲、入耳试，主要卖给游戏玩家。"
    )

    assert compiled.structured_request["confirmed_features"] == [
        "蓝牙5.3",
        "游戏低延迟",
        "长续航",
        "快充",
    ]
    assert compiled.structured_request["confirmed_product_form"] == "入耳式"


def test_colloquial_words_without_numbers_do_not_invent_critical_fields() -> None:
    adapter = SemanticAdapter([RuntimeError("temporary model outage")])

    compiled = RequestCompiler(adapter).compile(  # type: ignore[arg-type]
        "帮我上架无线耳机，进货渠道还没定，想卖得更好，库存管理很重要。"
    )

    assert compiled.assessment.mode is RequestMode.clarify
    assert compiled.structured_request["cost"] is None
    assert compiled.structured_request["target_price"] is None
    assert compiled.structured_request["inventory"] is None
