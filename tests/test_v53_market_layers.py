from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.orchestration.artifacts import ResearchEvidence, artifact_from_result
from app.orchestration.workflow import run_workflow
from app.tools.market_data import load_products, load_reviews
from app.tools.market_layers import ComparableMarketInput, classify_market_layers
from app.tools.market_statistics import clean_market_price_samples
from app.tools.product_tools import build_market_report


GAME_FEATURES = ["蓝牙5.3", "游戏低延迟", "长续航", "快充", "通话降噪"]


def _product(product_id: str, price: float, **overrides) -> dict:
    return {
        "id": product_id,
        "name": product_id,
        "category": "无线耳机",
        "price": price,
        "features": ["蓝牙5.3"],
        "target_audience": ["游戏"],
        "condition": "new",
        "brand_tier": "mass_market",
        "product_type": "earbuds",
        "package_type": "single_product",
        "sales_context": "regular",
        "currency": "CNY",
        "price_unit": "yuan_per_item",
        **overrides,
    }


def _classify(products: list[dict], **input_overrides):
    reviews = [
        {"product_id": product["id"], "rating": 4, "text": product["id"]}
        for product in products
    ]
    comparison_input = ComparableMarketInput(
        category="无线耳机",
        confirmed_features=("蓝牙5.3",),
        target_audience="游戏爱好者",
        **input_overrides,
    )
    return classify_market_layers(
        comparison_input,
        products,
        reviews,
        clean_market_price_samples(products),
    )


def test_v53_comparison_input_forbids_target_price() -> None:
    with pytest.raises(ValidationError):
        ComparableMarketInput.model_validate(
            {"category": "无线耳机", "target_price": 699}
        )


def test_v53_normal_gaming_market_separates_core_high_end_and_clearance() -> None:
    report = build_market_report(
        "无线耳机",
        target_audience="游戏爱好者",
        confirmed_features=GAME_FEATURES,
    )
    layers = report["market_layers"]
    decisions = {item["sample_id"]: item for item in layers["decisions"]}

    assert report["sample_size"]["valid_competitors"] == 98
    assert report["sample_size"]["competitors"] >= 90
    assert layers["core_comparable"]["sample_count"] == report["sample_size"]["competitors"]
    assert layers["full_valid_market"]["sample_count"] == 98
    assert decisions["earbud_097"]["assigned_layer"] == "adjacent_tier"
    assert decisions["earbud_097"]["adjacent_group"] == "premium_or_luxury"
    assert decisions["earbud_095"]["assigned_layer"] == "adjacent_tier"
    assert decisions["earbud_095"]["adjacent_group"] == "entry_or_clearance"
    assert report["price_band"] == [129.0, 239.0]
    assert report["full_market_band"] == [49.0, 899.0]
    assert report["core_reference_price"] < 200


def test_v53_target_price_changes_do_not_change_market_selection() -> None:
    common = (
        "成本95元的无线耳机，主要面向游戏爱好者，库存800件，最低毛利率25%。"
        "已确认功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
    )
    lower = run_workflow(f"我要上架一款{common}目标售价199元。", approved=False)
    higher = run_workflow(f"我要上架一款{common}目标售价699元。", approved=False)

    lower_market = lower.agent_outputs["market_agent"]["market_layers"]
    higher_market = higher.agent_outputs["market_agent"]["market_layers"]
    assert lower_market["core_comparable"]["product_ids"] == higher_market[
        "core_comparable"
    ]["product_ids"]
    assert lower_market["content_hash"] == higher_market["content_hash"]


def test_v53_explainable_extreme_can_be_core_when_target_is_same_tier() -> None:
    products = load_products("无线耳机")
    evidence = classify_market_layers(
        ComparableMarketInput(
            category="无线耳机",
            confirmed_features=("自适应主动降噪", "空间音频"),
            target_audience="商务",
            brand_tier="premium",
        ),
        products,
        load_reviews("无线耳机"),
        clean_market_price_samples(products),
    )
    decisions = {item.sample_id: item for item in evidence.decisions}

    assert decisions["earbud_097"].assigned_layer == "core_comparable"
    assert evidence.core_comparable.product_ids == ("earbud_097",)
    assert evidence.mode == "advisory_only"
    assert "insufficient_core_comparables" in evidence.warnings


def test_v53_skewed_core_uses_median_reference() -> None:
    evidence = _classify(
        [_product(f"p{i}", price) for i, price in enumerate([100, 100, 100, 100, 200])]
    )

    assert evidence.distribution_status == "skewed"
    assert evidence.reference_method == "core_cleaned_median"
    assert evidence.core_reference_price == 100
    assert evidence.mode == "decision_ready"


def test_v53_small_or_multimodal_core_is_advisory_only() -> None:
    small = _classify([_product(f"s{i}", 100 + i) for i in range(4)])
    multimodal = _classify(
        [_product(f"m{i}", price) for i, price in enumerate([100] * 5 + [500] * 5)]
    )

    assert small.mode == "advisory_only"
    assert small.distribution_status == "insufficient"
    assert multimodal.mode == "advisory_only"
    assert multimodal.distribution_status == "multimodal"


def test_v53_reviews_follow_their_product_layer() -> None:
    products = [
        *[_product(f"core_{i}", 100 + i) for i in range(5)],
        _product(
            "premium",
            699,
            brand_tier="premium",
            features=["空间音频"],
            target_audience=["商务"],
        ),
    ]
    reviews = [
        {"product_id": product["id"], "rating": 4, "text": product["id"]}
        for product in products
    ]
    evidence = classify_market_layers(
        ComparableMarketInput(
            category="无线耳机",
            confirmed_features=("蓝牙5.3",),
            target_audience="游戏",
        ),
        products,
        reviews,
        clean_market_price_samples(products),
    )

    assert evidence.core_comparable.review_count == 5
    assert "premium" not in evidence.core_comparable.review_product_ids
    assert evidence.adjacent_tier.review_product_ids == ("premium",)
    assert evidence.full_valid_market.review_count == 6


def test_v53_layering_is_deterministic_across_input_order() -> None:
    products = [_product(f"p{i}", 100 + i * 10) for i in range(10)]
    forward = _classify(products)
    reverse = _classify(list(reversed(products)))

    assert forward == reverse
    assert forward.content_hash == reverse.content_hash


def test_v53_market_artifact_contains_recomputable_three_layer_evidence() -> None:
    report = build_market_report(
        "无线耳机",
        target_audience="游戏爱好者",
        confirmed_features=GAME_FEATURES,
    )
    artifact = artifact_from_result(
        task_id="task_v53",
        producer="market_agent",
        result=report,
        input_state_version=1,
        confidence=0.9,
    )
    layers = artifact.market_layers

    assert isinstance(artifact, ResearchEvidence)
    assert layers["policy_version"] == "market-layering-v1"
    assert (
        layers["core_comparable"]["sample_count"]
        + layers["adjacent_tier"]["sample_count"]
        == layers["full_valid_market"]["sample_count"]
    )
    assert len(layers["decisions"]) == layers["full_valid_market"]["sample_count"]
    assert layers["content_hash"]
