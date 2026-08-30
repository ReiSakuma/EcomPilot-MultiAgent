from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestration.artifacts import ResearchEvidence, artifact_from_result
from app.tools.market_data import load_products
from app.tools.market_statistics import clean_market_price_samples
from app.tools.product_tools import build_market_report


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_DIR = ROOT / "data" / "products"
TEST_ONLY_FIELDS = {
    "test_case",
    "expected_statistical_flag",
    "expected_excluded",
    "expected_market_layer",
}


def _products(prices: list[float], **extra) -> list[dict]:
    return [
        {"id": f"p_{index:03d}", "price": price, **extra}
        for index, price in enumerate(prices)
    ]


@pytest.mark.parametrize(
    ("category", "dirty_ids", "explainable_ids"),
    [
        ("无线耳机", {"earbud_099", "earbud_100"}, {f"earbud_{i:03d}" for i in range(95, 99)}),
        ("机械键盘", {"keyboard_099", "keyboard_100"}, {f"keyboard_{i:03d}" for i in range(95, 99)}),
    ],
)
def test_v52_fixture_decisions_distinguish_dirty_from_explainable(
    category: str, dirty_ids: set[str], explainable_ids: set[str]
) -> None:
    result = clean_market_price_samples(load_products(category))
    decisions = {item.sample_id: item for item in result.decisions}

    assert {item.sample_id for item in result.decisions if item.excluded} == dirty_ids
    assert all(decisions[item].status == "dirty_outlier" for item in dirty_ids)
    assert all(
        decisions[item].status == "explainable_extreme"
        and not decisions[item].excluded
        and decisions[item].statistical_flags
        for item in explainable_ids
    )
    assert all(
        not item.excluded
        for item in result.decisions
        if item.sample_id not in dirty_ids
    )


def test_v52_high_end_earbud_is_flagged_but_retained() -> None:
    decisions = {
        item.sample_id: item
        for item in clean_market_price_samples(load_products("无线耳机")).decisions
    }
    premium = decisions["earbud_097"]

    assert premium.original_price == 699
    assert premium.status == "explainable_extreme"
    assert not premium.excluded
    assert "brand_tier:premium" in premium.business_explanations


def test_v52_flat_distribution_handles_zero_mad_and_iqr() -> None:
    result = clean_market_price_samples(_products([100] * 10))

    assert result.mode == "decision_ready"
    assert result.cleaned_distribution.iqr == 0
    assert result.cleaned_distribution.log_mad == 0
    assert all(not item.statistical_flags for item in result.decisions)
    assert all(item.status == "retained" for item in result.decisions)


def test_v52_fewer_than_five_samples_is_advisory_only() -> None:
    result = clean_market_price_samples(_products([100, 110, 120, 9999]))

    assert result.mode == "advisory_only"
    assert "insufficient_input_samples" in result.warnings
    assert all(not item.statistical_flags for item in result.decisions)


def test_v52_duplicate_sku_keeps_one_canonical_record() -> None:
    products = _products([100, 110, 120, 130, 140])
    products.extend(
        [
            {"id": "duplicate_b", "sku": "same-sku", "price": 199},
            {"id": "duplicate_a", "sku": "same-sku", "price": 189},
        ]
    )
    result = clean_market_price_samples(products)
    duplicates = [
        item for item in result.decisions if item.identity_key == "same-sku"
    ]

    assert len(duplicates) == 2
    assert sum(item.excluded for item in duplicates) == 1
    assert any("duplicate_identity" in item.reason_codes for item in duplicates)


@pytest.mark.parametrize(
    ("product", "reason"),
    [
        ({"id": "zero", "price": 0}, "non_positive_price"),
        ({"id": "negative", "price": -1}, "non_positive_price"),
        ({"id": "currency", "price": 100, "currency": "USD"}, "unsupported_currency"),
        ({"id": "unit", "price": 100, "price_unit": "per_box_unknown"}, "unsupported_price_unit"),
        (
            {"id": "bundle", "price": 100, "package_type": "bundle"},
            "missing_bundle_size",
        ),
    ],
)
def test_v52_invalid_values_have_explicit_decisions(product: dict, reason: str) -> None:
    result = clean_market_price_samples([product, *_products([10, 20, 30, 40, 50])])
    decision = next(item for item in result.decisions if item.sample_id == product["id"])

    assert decision.status == "dirty_outlier"
    assert decision.excluded
    assert reason in decision.reason_codes


def test_v52_normalizes_fen_bundle_and_condition() -> None:
    products = _products([100, 110, 120, 130, 140])
    products.append(
        {
            "id": "bundle_fen",
            "price": 30000,
            "currency": "RMB",
            "price_unit": "fen_per_item",
            "package_type": "bundle",
            "package_size": 3,
            "condition": "官方翻新",
        }
    )
    decision = next(
        item
        for item in clean_market_price_samples(products).decisions
        if item.sample_id == "bundle_fen"
    )

    assert decision.normalized_price == 100
    assert decision.normalized_currency == "CNY"
    assert decision.normalized_package_type == "bundle"
    assert decision.normalized_condition == "refurbished"
    assert not decision.excluded


def test_v52_overcleaning_and_multimodal_distributions_degrade() -> None:
    dirty = [
        {
            "id": f"dirty_{index}",
            "price": 10 + index,
            "product_type": "accessory",
        }
        for index in range(8)
    ]
    overcleaned = clean_market_price_samples([*_products([100, 110]), *dirty])
    multimodal = clean_market_price_samples(_products([100] * 5 + [500] * 5))

    assert overcleaned.mode == "advisory_only"
    assert "insufficient_retained_samples" in overcleaned.warnings
    assert "retained_ratio_below_minimum" in overcleaned.warnings
    assert multimodal.mode == "advisory_only"
    assert multimodal.cleaned_distribution.multimodal_suspected
    assert "multimodal_distribution_suspected" in multimodal.warnings


def test_v52_result_and_hash_are_deterministic_across_input_order() -> None:
    products = _products([89, 99, 109, 119, 129, 139, 149, 9999])
    forward = clean_market_price_samples(products)
    reverse = clean_market_price_samples(list(reversed(products)))

    assert forward == reverse
    assert forward.content_hash == reverse.content_hash


@pytest.mark.parametrize(
    ("filename", "dirty_ids"),
    [
        ("wireless_earbuds_competitors.json", {"earbud_099", "earbud_100"}),
        ("mechanical_keyboards_competitors.json", {"keyboard_099", "keyboard_100"}),
    ],
)
def test_v52_decisions_do_not_depend_on_offline_expected_labels(
    filename: str, dirty_ids: set[str]
) -> None:
    raw = json.loads((PRODUCT_DIR / filename).read_text(encoding="utf-8"))
    stripped = [
        {key: value for key, value in product.items() if key not in TEST_ONLY_FIELDS}
        for product in raw
    ]
    result = clean_market_price_samples(stripped)

    assert {item.sample_id for item in result.decisions if item.excluded} == dirty_ids


def test_v52_market_report_and_artifact_keep_complete_cleaning_audit() -> None:
    report = build_market_report("无线耳机")
    artifact = artifact_from_result(
        task_id="task_v52",
        producer="market_agent",
        result=report,
        input_state_version=1,
        confidence=0.9,
    )

    assert report["sample_size"]["raw_competitors"] == 100
    assert report["sample_size"]["valid_competitors"] == 98
    assert report["sample_size"]["excluded_competitors"] == 2
    assert report["raw_price_band"] == [9.9, 9999.0]
    cleaned = report["market_statistics"]["cleaned_distribution"]
    assert [cleaned["minimum"], cleaned["maximum"]] == [49.0, 899.0]
    assert report["market_statistics"]["input_count"] == 100
    assert len(report["market_statistics"]["decisions"]) == 100
    assert isinstance(artifact, ResearchEvidence)
    assert artifact.market_statistics["content_hash"]
