from __future__ import annotations

import hashlib
import json
import math
from statistics import mean, median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DecisionStatus = Literal[
    "dirty_outlier", "explainable_extreme", "suspicious", "retained"
]
StatisticsMode = Literal["decision_ready", "advisory_only"]

SUPPORTED_CURRENCIES = {"CNY", "RMB", "人民币", "¥"}
YUAN_UNITS = {"yuan", "yuan_per_item", "cny", "元", "元/件"}
FEN_UNITS = {"fen", "fen_per_item", "分", "分/件"}
AMBIGUOUS_UNITS = {"fen_mislabeled_as_yuan"}
HARD_DATA_QUALITY_FLAGS = {
    "category_mismatch",
    "non_product_accessory",
    "currency_unit_mismatch",
    "suspected_import_error",
}
EXPLAINABLE_CONTEXTS = {
    "clearance",
    "refurbished_clearance",
    "made_to_order",
}
EXPLAINABLE_TIERS = {"premium", "luxury"}


class PriceDistribution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    count: int = Field(ge=0)
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    median: float | None = None
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    log_mad: float | None = None
    mean_median_gap_rate: float | None = None
    multimodal_suspected: bool = False


class MarketSampleDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str
    identity_key: str
    source_hash: str
    original_price: float | None = None
    normalized_price: float | None = None
    normalized_currency: str | None = None
    normalized_condition: Literal["new", "refurbished", "used", "unknown"]
    normalized_package_type: Literal["single", "bundle", "accessory", "unknown"]
    status: DecisionStatus
    excluded: bool
    statistical_flags: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    business_explanations: tuple[str, ...] = ()
    normalization_notes: tuple[str, ...] = ()


class MarketStatisticsResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    policy_version: Literal["market-cleaning-v1"] = "market-cleaning-v1"
    mode: StatisticsMode
    input_count: int = Field(ge=0)
    normalized_count: int = Field(ge=0)
    retained_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    retained_ratio: float = Field(ge=0, le=1)
    raw_distribution: PriceDistribution
    cleaned_distribution: PriceDistribution
    decisions: tuple[MarketSampleDecision, ...]
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


def clean_market_price_samples(
    products: list[dict[str, Any]],
    *,
    minimum_retained: int = 5,
    minimum_retained_ratio: float = 0.7,
) -> MarketStatisticsResult:
    """Normalize and conservatively classify market prices without model calls."""
    prepared = [_prepare_product(product, index) for index, product in enumerate(products)]
    duplicate_rows = _duplicate_rows(prepared)
    statistical_population = [
        item["normalized_price"]
        for item in prepared
        if item["normalized_price"] is not None
    ]
    bounds = _statistical_bounds(statistical_population)
    decisions: list[MarketSampleDecision] = []

    for item in prepared:
        hard_reasons = list(item["hard_reasons"])
        if id(item) in duplicate_rows:
            hard_reasons.append("duplicate_identity")
        statistical_flags = _statistical_flags(item["normalized_price"], bounds)
        explanations = _business_explanations(item["product"])

        if hard_reasons:
            status: DecisionStatus = "dirty_outlier"
            excluded = True
        elif statistical_flags and explanations:
            status = "explainable_extreme"
            excluded = False
        elif statistical_flags:
            status = "suspicious"
            excluded = False
        else:
            status = "retained"
            excluded = False

        decisions.append(
            MarketSampleDecision(
                sample_id=item["sample_id"],
                identity_key=item["identity_key"],
                source_hash=item["source_hash"],
                original_price=item["original_price"],
                normalized_price=item["normalized_price"],
                normalized_currency=(
                    "CNY" if item["normalized_price"] is not None else None
                ),
                normalized_condition=item["normalized_condition"],
                normalized_package_type=item["normalized_package_type"],
                status=status,
                excluded=excluded,
                statistical_flags=tuple(sorted(statistical_flags)),
                reason_codes=tuple(sorted(set(hard_reasons))),
                business_explanations=tuple(explanations),
                normalization_notes=tuple(item["normalization_notes"]),
            )
        )

    decisions.sort(key=lambda item: (item.sample_id, item.source_hash))
    raw_prices = [
        item["original_price"]
        for item in prepared
        if item["original_price"] is not None
    ]
    retained_prices = [
        item.normalized_price
        for item in decisions
        if not item.excluded and item.normalized_price is not None
    ]
    raw_distribution = _distribution(raw_prices)
    cleaned_distribution = _distribution(retained_prices)
    retained_count = sum(not item.excluded for item in decisions)
    retained_ratio = retained_count / len(products) if products else 0.0
    warnings: list[str] = []
    if len(products) < minimum_retained:
        warnings.append("insufficient_input_samples")
    if retained_count < minimum_retained:
        warnings.append("insufficient_retained_samples")
    if retained_ratio < minimum_retained_ratio:
        warnings.append("retained_ratio_below_minimum")
    if cleaned_distribution.mean_median_gap_rate is not None and (
        cleaned_distribution.mean_median_gap_rate > 0.05
    ):
        warnings.append("mean_median_gap_unstable")
    if cleaned_distribution.multimodal_suspected:
        warnings.append("multimodal_distribution_suspected")

    return MarketStatisticsResult(
        mode="advisory_only" if warnings else "decision_ready",
        input_count=len(products),
        normalized_count=sum(
            item["normalized_price"] is not None for item in prepared
        ),
        retained_count=retained_count,
        excluded_count=len(products) - retained_count,
        retained_ratio=round(retained_ratio, 6),
        raw_distribution=raw_distribution,
        cleaned_distribution=cleaned_distribution,
        decisions=tuple(decisions),
        warnings=tuple(warnings),
    )


def summarize_price_distribution(prices: list[float]) -> PriceDistribution:
    """Public deterministic distribution helper used after market-layer selection."""
    return _distribution(prices)


def _prepare_product(product: dict[str, Any], index: int) -> dict[str, Any]:
    canonical = json.dumps(
        product, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sample_id = str(product.get("id") or product.get("sku") or f"row_{index:06d}")
    identity_key = str(product.get("sku") or product.get("id") or source_hash)
    original_price = _finite_number(product.get("price"))
    normalized_price, normalization_reasons = _normalize_price(product, original_price)
    normalized_condition, condition_note = _normalize_condition(product.get("condition"))
    normalized_package_type, package_note = _normalize_package_type(
        product.get("package_type")
    )
    flags = {
        str(flag)
        for flag in product.get("data_quality_flags", [])
        if str(flag) in HARD_DATA_QUALITY_FLAGS
    }
    hard_reasons = [*normalization_reasons, *(f"source_flag:{flag}" for flag in flags)]
    if str(product.get("product_type", "")).lower() == "accessory":
        hard_reasons.append("non_product_accessory")
    if str(product.get("package_type", "")).lower() in {
        "accessory",
        "accessory_pack",
    }:
        hard_reasons.append("non_product_accessory")
    return {
        "product": product,
        "sample_id": sample_id,
        "identity_key": identity_key,
        "source_hash": source_hash,
        "original_price": original_price,
        "normalized_price": normalized_price,
        "normalized_condition": normalized_condition,
        "normalized_package_type": normalized_package_type,
        "normalization_notes": [
            note for note in (condition_note, package_note) if note is not None
        ],
        "hard_reasons": hard_reasons,
    }


def _normalize_price(
    product: dict[str, Any], original_price: float | None
) -> tuple[float | None, list[str]]:
    if original_price is None:
        return None, ["invalid_price"]
    if original_price <= 0:
        return None, ["non_positive_price"]
    currency = str(product.get("currency", "CNY")).strip()
    if currency.upper() not in SUPPORTED_CURRENCIES and currency not in SUPPORTED_CURRENCIES:
        return None, ["unsupported_currency"]
    unit = str(product.get("price_unit", "yuan_per_item")).strip().lower()
    if unit in AMBIGUOUS_UNITS:
        return None, ["ambiguous_price_unit"]
    if unit in YUAN_UNITS:
        normalized = original_price
    elif unit in FEN_UNITS:
        normalized = original_price / 100
    else:
        return None, ["unsupported_price_unit"]
    package_type, _ = _normalize_package_type(product.get("package_type"))
    if package_type == "bundle" and product.get("package_size") is None:
        return None, ["missing_bundle_size"]
    package_size = _finite_number(product.get("package_size", 1))
    if package_size is None or package_size <= 0:
        return None, ["invalid_package_size"]
    normalized /= package_size
    if not math.isfinite(normalized) or normalized <= 0:
        return None, ["invalid_normalized_price"]
    return round(normalized, 6), []


def _normalize_condition(value: Any) -> tuple[str, str | None]:
    normalized = str(value or "new").strip().lower()
    if normalized in {"new", "新品", "全新"}:
        return "new", None
    if normalized in {"refurbished", "翻新", "官方翻新"}:
        return "refurbished", None
    if normalized in {"used", "二手"}:
        return "used", None
    return "unknown", "unknown_condition"


def _normalize_package_type(value: Any) -> tuple[str, str | None]:
    normalized = str(value or "single_product").strip().lower()
    if normalized in {"single", "single_product", "单品", "单件"}:
        return "single", None
    if normalized in {"bundle", "multipack", "套装", "组合装"}:
        return "bundle", None
    if normalized in {"accessory", "accessory_pack", "配件"}:
        return "accessory", None
    return "unknown", "unknown_package_type"


def _duplicate_rows(prepared: list[dict[str, Any]]) -> set[int]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in prepared:
        groups.setdefault(item["identity_key"], []).append(item)
    duplicates: set[int] = set()
    for group in groups.values():
        if len(group) <= 1:
            continue
        canonical = min(group, key=lambda item: item["source_hash"])
        skipped_canonical = False
        for item in sorted(group, key=lambda row: row["source_hash"]):
            if item is canonical and not skipped_canonical:
                skipped_canonical = True
                continue
            duplicates.add(id(item))
    return duplicates


def _business_explanations(product: dict[str, Any]) -> list[str]:
    explanation = str(product.get("price_explanation") or "").strip()
    if not explanation:
        return []
    reasons: list[str] = []
    context = str(product.get("sales_context") or "").lower()
    tier = str(product.get("brand_tier") or "").lower()
    condition = str(product.get("condition") or "").lower()
    if context in EXPLAINABLE_CONTEXTS:
        reasons.append(f"sales_context:{context}")
    if tier in EXPLAINABLE_TIERS:
        reasons.append(f"brand_tier:{tier}")
    if condition in {"refurbished", "used"}:
        reasons.append(f"condition:{condition}")
    if reasons:
        reasons.append("documented_price_explanation")
    return reasons


def _statistical_bounds(prices: list[float]) -> dict[str, float | None]:
    if len(prices) < 5:
        return {"lower": None, "upper": None, "log_median": None, "log_mad": None}
    ordered = sorted(prices)
    q1, q3 = _quartiles(ordered)
    iqr = q3 - q1
    logs = [math.log(price) for price in ordered]
    log_median = median(logs)
    log_mad = median(abs(value - log_median) for value in logs)
    return {
        "lower": q1 - 1.5 * iqr if iqr > 0 else None,
        "upper": q3 + 1.5 * iqr if iqr > 0 else None,
        "log_median": log_median,
        "log_mad": log_mad if log_mad > 0 else None,
    }


def _statistical_flags(
    price: float | None, bounds: dict[str, float | None]
) -> list[str]:
    if price is None:
        return []
    flags: list[str] = []
    lower, upper = bounds["lower"], bounds["upper"]
    if lower is not None and upper is not None and (price < lower or price > upper):
        flags.append("iqr_outlier")
    if bounds["log_mad"] is not None and bounds["log_median"] is not None:
        modified_z = abs(
            0.6745
            * (math.log(price) - float(bounds["log_median"]))
            / float(bounds["log_mad"])
        )
        if modified_z > 3.5:
            flags.append("log_mad_outlier")
    return flags


def _distribution(prices: list[float]) -> PriceDistribution:
    if not prices:
        return PriceDistribution(count=0)
    ordered = sorted(float(price) for price in prices)
    q1, q3 = _quartiles(ordered)
    midpoint = median(ordered)
    average = mean(ordered)
    logs = [math.log(price) for price in ordered if price > 0]
    log_midpoint = median(logs) if logs else None
    log_mad = (
        median(abs(value - log_midpoint) for value in logs)
        if log_midpoint is not None
        else None
    )
    gap_rate = abs(average - midpoint) / midpoint if midpoint else None
    return PriceDistribution(
        count=len(ordered),
        minimum=round(ordered[0], 6),
        maximum=round(ordered[-1], 6),
        mean=round(average, 6),
        median=round(midpoint, 6),
        q1=round(q1, 6),
        q3=round(q3, 6),
        iqr=round(q3 - q1, 6),
        log_mad=round(log_mad, 6) if log_mad is not None else None,
        mean_median_gap_rate=round(gap_rate, 6) if gap_rate is not None else None,
        multimodal_suspected=_multimodal_suspected(logs),
    )


def _quartiles(ordered: list[float]) -> tuple[float, float]:
    if len(ordered) == 1:
        return ordered[0], ordered[0]
    half = len(ordered) // 2
    lower = ordered[:half]
    upper = ordered[-half:]
    return median(lower), median(upper)


def _multimodal_suspected(log_prices: list[float]) -> bool:
    if len(log_prices) < 8:
        return False
    ordered = sorted(log_prices)
    gaps = [right - left for left, right in zip(ordered, ordered[1:])]
    positive_gaps = [gap for gap in gaps if gap > 1e-9]
    if not positive_gaps:
        return False
    baseline = median(positive_gaps) if len(positive_gaps) >= 3 else 0.0
    threshold = max(math.log(1.5), baseline * 8)
    for index, gap in enumerate(gaps):
        left_count = index + 1
        right_count = len(ordered) - left_count
        if gap > threshold and left_count >= 3 and right_count >= 3:
            return True
    return False


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
