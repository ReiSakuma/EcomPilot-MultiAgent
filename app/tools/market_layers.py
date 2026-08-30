from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tools.market_data import normalize_market_category
from app.tools.market_statistics import (
    MarketStatisticsResult,
    PriceDistribution,
    summarize_price_distribution,
)


LayerName = Literal["core_comparable", "adjacent_tier"]
EvidenceMode = Literal["decision_ready", "advisory_only"]
ReferenceMethod = Literal[
    "core_cleaned_mean", "core_cleaned_median", "unavailable"
]
DistributionStatus = Literal["stable", "skewed", "multimodal", "insufficient"]


class ComparableMarketInput(BaseModel):
    """Trusted comparison facts. Target price is intentionally not part of this schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str = Field(min_length=1, max_length=80)
    confirmed_product_form: str | None = Field(default=None, max_length=80)
    confirmed_features: tuple[str, ...] = ()
    target_audience: str | None = Field(default=None, max_length=80)
    channel: str = Field(default="general_ecommerce", min_length=1, max_length=80)
    condition: str = Field(default="new", min_length=1, max_length=40)
    brand_tier: str = Field(default="mass_market", min_length=1, max_length=40)


class MarketLayerDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str
    assigned_layer: LayerName
    adjacent_group: str | None = None
    match_score: float = Field(ge=0, le=1)
    feature_overlap_rate: float | None = Field(default=None, ge=0, le=1)
    audience_match: bool | None = None
    matched_dimensions: tuple[str, ...] = ()
    mismatch_reasons: tuple[str, ...] = ()


class MarketLayerEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layer: Literal["core_comparable", "adjacent_tier", "full_valid_market"]
    sample_count: int = Field(ge=0)
    product_ids: tuple[str, ...]
    review_count: int = Field(ge=0)
    review_product_ids: tuple[str, ...]
    price_distribution: PriceDistribution


class ComparableMarketEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    policy_version: Literal["market-layering-v1"] = "market-layering-v1"
    comparison_input: ComparableMarketInput
    mode: EvidenceMode
    distribution_status: DistributionStatus
    core_reference_price: float | None = None
    reference_method: ReferenceMethod
    core_comparable: MarketLayerEvidence
    adjacent_tier: MarketLayerEvidence
    full_valid_market: MarketLayerEvidence
    adjacent_tier_bands: dict[str, PriceDistribution] = Field(default_factory=dict)
    decisions: tuple[MarketLayerDecision, ...]
    warnings: tuple[str, ...] = ()
    content_hash: str = ""

    @model_validator(mode="after")
    def assign_content_hash(self):
        if self.content_hash:
            return self
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        object.__setattr__(
            self,
            "content_hash",
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        return self


def classify_market_layers(
    comparison_input: ComparableMarketInput,
    products: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    cleaning: MarketStatisticsResult,
    *,
    minimum_core_samples: int = 5,
) -> ComparableMarketEvidence:
    """Classify valid products without reading or inferring the user's target price."""
    valid_prices = {
        decision.sample_id: decision.normalized_price
        for decision in cleaning.decisions
        if not decision.excluded and decision.normalized_price is not None
    }
    valid_products = [
        product for product in products if str(product.get("id")) in valid_prices
    ]
    target = _normalized_target(comparison_input)
    decisions: list[MarketLayerDecision] = []
    adjacent_groups: dict[str, list[float]] = {}

    for product in valid_products:
        decision = _classify_product(target, product)
        decisions.append(decision)
        if decision.assigned_layer == "adjacent_tier":
            adjacent_groups.setdefault(decision.adjacent_group or "other", []).append(
                float(valid_prices[decision.sample_id])
            )

    decisions.sort(key=lambda item: item.sample_id)
    core_ids = tuple(
        item.sample_id for item in decisions if item.assigned_layer == "core_comparable"
    )
    adjacent_ids = tuple(
        item.sample_id for item in decisions if item.assigned_layer == "adjacent_tier"
    )
    full_ids = tuple(sorted(valid_prices))
    core_distribution = summarize_price_distribution(
        [float(valid_prices[item]) for item in core_ids]
    )
    adjacent_distribution = summarize_price_distribution(
        [float(valid_prices[item]) for item in adjacent_ids]
    )
    full_distribution = summarize_price_distribution(
        [float(valid_prices[item]) for item in full_ids]
    )

    warnings: list[str] = []
    if core_distribution.count < minimum_core_samples:
        distribution_status: DistributionStatus = "insufficient"
        warnings.append("insufficient_core_comparables")
    elif core_distribution.multimodal_suspected:
        distribution_status = "multimodal"
        warnings.append("core_multimodal_distribution")
    elif (
        core_distribution.mean_median_gap_rate is not None
        and core_distribution.mean_median_gap_rate > 0.05
    ):
        distribution_status = "skewed"
    else:
        distribution_status = "stable"

    if core_distribution.count == 0:
        reference_price = None
        reference_method: ReferenceMethod = "unavailable"
    elif (
        distribution_status == "stable"
        and core_distribution.mean is not None
        and core_distribution.median is not None
    ):
        reference_price = core_distribution.mean
        reference_method = "core_cleaned_mean"
    else:
        reference_price = core_distribution.median
        reference_method = "core_cleaned_median"

    mode: EvidenceMode = (
        "advisory_only"
        if distribution_status in {"insufficient", "multimodal"}
        else "decision_ready"
    )
    return ComparableMarketEvidence(
        comparison_input=comparison_input,
        mode=mode,
        distribution_status=distribution_status,
        core_reference_price=reference_price,
        reference_method=reference_method,
        core_comparable=_layer_evidence(
            "core_comparable", core_ids, valid_prices, reviews
        ),
        adjacent_tier=_layer_evidence(
            "adjacent_tier", adjacent_ids, valid_prices, reviews
        ),
        full_valid_market=_layer_evidence(
            "full_valid_market", full_ids, valid_prices, reviews
        ),
        adjacent_tier_bands={
            key: summarize_price_distribution(values)
            for key, values in sorted(adjacent_groups.items())
        },
        decisions=tuple(decisions),
        warnings=tuple(warnings),
    )


def _normalized_target(source: ComparableMarketInput) -> dict[str, Any]:
    category = normalize_market_category(source.category)
    product_type = _normalize_product_type(source.confirmed_product_form, category)
    return {
        "category": category,
        "product_type": product_type,
        "features": tuple(
            value for value in (_normalize_text(item) for item in source.confirmed_features) if value
        ),
        "audience": _normalize_audience(source.target_audience),
        "channel": _normalize_text(source.channel),
        "condition": _normalize_condition(source.condition),
        "brand_tier": _normalize_text(source.brand_tier),
    }


def _classify_product(target: dict[str, Any], product: dict[str, Any]) -> MarketLayerDecision:
    sample_id = str(product.get("id"))
    candidate = {
        "category": normalize_market_category(str(product.get("category") or "")),
        "product_type": _normalize_product_type(product.get("product_type"), str(product.get("category") or "")),
        "package": _normalize_package(product.get("package_type")),
        "channel": _normalize_text(product.get("channel") or "general_ecommerce"),
        "condition": _normalize_condition(product.get("condition")),
        "brand_tier": _normalize_text(product.get("brand_tier") or "mass_market"),
        "sales_context": _normalize_text(product.get("sales_context") or "regular"),
    }
    feature_overlap = _feature_overlap(
        target["features"], tuple(product.get("features") or ())
    )
    audience_match = _audience_matches(
        target["audience"], tuple(product.get("target_audience") or ())
    )
    dimensions: list[tuple[str, bool, float]] = [
        ("category", candidate["category"] == target["category"], 0.20),
        ("product_type", candidate["product_type"] == target["product_type"], 0.15),
        ("package", candidate["package"] == "single", 0.05),
        ("channel", candidate["channel"] == target["channel"], 0.05),
        ("condition", candidate["condition"] == target["condition"], 0.10),
        ("brand_tier", candidate["brand_tier"] == target["brand_tier"], 0.15),
        ("sales_context", candidate["sales_context"] == "regular", 0.10),
    ]
    if target["audience"] is not None:
        dimensions.append(("target_audience", bool(audience_match), 0.05))
    if target["features"]:
        dimensions.append(("confirmed_features", bool(feature_overlap and feature_overlap >= 0.2), 0.15))
    total_weight = sum(weight for _, _, weight in dimensions)
    matched_weight = sum(weight for _, matched, weight in dimensions if matched)
    score = round(matched_weight / total_weight, 6) if total_weight else 0.0
    matched = tuple(name for name, value, _ in dimensions if value)
    mismatches = tuple(f"{name}_mismatch" for name, value, _ in dimensions if not value)

    structural_match = all(
        value
        for name, value, _ in dimensions
        if name in {"category", "product_type", "package", "channel"}
    )
    tier_match = all(
        value
        for name, value, _ in dimensions
        if name in {"condition", "brand_tier", "sales_context"}
    )
    has_relevance_input = bool(target["features"] or target["audience"])
    relevant = (
        not has_relevance_input
        or bool(audience_match)
        or bool(feature_overlap is not None and feature_overlap >= 0.2)
    )
    is_core = structural_match and tier_match and relevant
    return MarketLayerDecision(
        sample_id=sample_id,
        assigned_layer="core_comparable" if is_core else "adjacent_tier",
        adjacent_group=None if is_core else _adjacent_group(candidate, mismatches),
        match_score=score,
        feature_overlap_rate=feature_overlap,
        audience_match=audience_match,
        matched_dimensions=matched,
        mismatch_reasons=mismatches,
    )


def _layer_evidence(
    layer: Literal["core_comparable", "adjacent_tier", "full_valid_market"],
    product_ids: tuple[str, ...],
    prices: dict[str, float | None],
    reviews: list[dict[str, Any]],
) -> MarketLayerEvidence:
    allowed = set(product_ids)
    layer_reviews = [review for review in reviews if str(review.get("product_id")) in allowed]
    review_product_ids = tuple(sorted({str(review.get("product_id")) for review in layer_reviews}))
    return MarketLayerEvidence(
        layer=layer,
        sample_count=len(product_ids),
        product_ids=tuple(sorted(product_ids)),
        review_count=len(layer_reviews),
        review_product_ids=review_product_ids,
        price_distribution=summarize_price_distribution(
            [float(prices[item]) for item in product_ids if prices.get(item) is not None]
        ),
    )


def _adjacent_group(candidate: dict[str, str], mismatches: tuple[str, ...]) -> str:
    if candidate["brand_tier"] in {"premium", "luxury"} or candidate["sales_context"] == "made_to_order":
        return "premium_or_luxury"
    if candidate["brand_tier"] == "entry" or candidate["sales_context"] in {"clearance", "refurbished_clearance"}:
        return "entry_or_clearance"
    if "product_type_mismatch" in mismatches:
        return "product_form"
    if "condition_mismatch" in mismatches:
        return "condition"
    return "other"


def _feature_overlap(target_features: tuple[str, ...], candidate_features: tuple[Any, ...]) -> float | None:
    if not target_features:
        return None
    candidates = tuple(_normalize_text(item) for item in candidate_features)
    matches = sum(
        any(target in candidate or candidate in target for candidate in candidates if candidate)
        for target in target_features
    )
    return round(matches / len(target_features), 6)


def _audience_matches(target: str | None, candidates: tuple[Any, ...]) -> bool | None:
    if target is None:
        return None
    normalized = {_normalize_audience(item) for item in candidates}
    return target in normalized


def _normalize_product_type(value: Any, category: str) -> str:
    normalized = _normalize_text(value)
    if not normalized or normalized in {"未确认", "不确定", "unknown"}:
        return "earbuds" if normalize_market_category(category) == "无线耳机" else "keyboard"
    if "头戴" in normalized:
        return "headphones"
    if "耳机" in normalized or normalized in {"earbuds", "tws", "入耳式"}:
        return "earbuds"
    if "键盘" in normalized or normalized == "keyboard":
        return "keyboard"
    return normalized


def _normalize_package(value: Any) -> str:
    normalized = _normalize_text(value or "single_product")
    if normalized in {"single", "singleproduct", "单品", "单件"}:
        return "single"
    if normalized in {"bundle", "multipack", "套装", "组合装"}:
        return "bundle"
    if normalized in {"accessory", "accessorypack", "配件"}:
        return "accessory"
    return normalized


def _normalize_condition(value: Any) -> str:
    normalized = _normalize_text(value or "new")
    if normalized in {"new", "新品", "全新"}:
        return "new"
    if normalized in {"refurbished", "翻新", "官方翻新"}:
        return "refurbished"
    if normalized in {"used", "二手"}:
        return "used"
    return normalized


def _normalize_audience(value: Any) -> str | None:
    normalized = _normalize_text(value)
    if not normalized:
        return None
    for suffix in ("用户", "人群", "群体", "消费者", "爱好者"):
        normalized = normalized.replace(suffix, "")
    if normalized in {"电竞", "游戏玩家", "打游戏"}:
        return "游戏"
    if normalized == "大学生":
        return "学生"
    return normalized


def _normalize_text(value: Any) -> str:
    return re.sub(r"[\s,，、;；/_-]+", "", str(value or "").strip().lower())
