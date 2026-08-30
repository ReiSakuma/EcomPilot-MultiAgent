from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import (
    MARKET_PRICE_HIGH_QUALITY_MIN_SAMPLES,
    MARKET_PRICE_MEDIUM_QUALITY_MIN_SAMPLES,
    MARKET_PRICE_THRESHOLD_COMMODITY,
    MARKET_PRICE_THRESHOLD_DIFFERENTIATED,
    MARKET_PRICE_THRESHOLD_STANDARD,
)


PricingProfile = Literal["commodity", "standard", "differentiated"]
AssessmentStatus = Literal[
    "passed", "confirmation_required", "advisory_only", "unavailable"
]
PricePosition = Literal[
    "below_market", "within_market", "above_market", "cost_market_conflict", "unavailable"
]
EvidenceQuality = Literal["high", "medium", "low", "unavailable"]
ConfirmationAction = Literal[
    "adopt_suggested_price",
    "keep_original_with_evidence",
    "market_analysis_only",
    "unknown",
]


THRESHOLDS: dict[str, float] = {
    "commodity": MARKET_PRICE_THRESHOLD_COMMODITY,
    "standard": MARKET_PRICE_THRESHOLD_STANDARD,
    "differentiated": MARKET_PRICE_THRESHOLD_DIFFERENTIATED,
}


class MarketPriceAssessmentInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_price: float = Field(gt=0)
    cost: float = Field(ge=0)
    min_margin_rate: float = Field(ge=0, lt=1)
    category: str = Field(min_length=1, max_length=80)
    pricing_profile: PricingProfile = "standard"
    core_reference_price: float | None = Field(default=None, gt=0)
    reference_method: str = "unavailable"
    core_mean_price: float | None = Field(default=None, gt=0)
    core_median_price: float | None = Field(default=None, gt=0)
    core_price_band: tuple[float, float] | None = None
    full_market_band: tuple[float, float] | None = None
    core_sample_count: int = Field(default=0, ge=0)
    excluded_sample_count: int = Field(default=0, ge=0)
    market_mode: Literal["decision_ready", "advisory_only"] = "advisory_only"
    distribution_status: Literal[
        "stable", "skewed", "multimodal", "insufficient"
    ] = "insufficient"
    pricing_override: bool = False
    pricing_override_evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_override_evidence(self):
        if self.pricing_override and not self.pricing_override_evidence:
            raise ValueError("pricing_override requires user-confirmed evidence")
        return self


class MarketPriceAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    policy_version: Literal["market-price-gate-v1"] = "market-price-gate-v1"
    status: AssessmentStatus
    position: PricePosition
    reason_code: str
    target_price: float
    cost: float
    min_margin_rate: float
    pricing_profile: PricingProfile
    threshold_rate: float = Field(ge=0, lt=1)
    core_reference_price: float | None = None
    reference_method: str
    core_mean_price: float | None = None
    core_median_price: float | None = None
    core_price_band: tuple[float, float] | None = None
    full_market_band: tuple[float, float] | None = None
    deviation_rate: float | None = None
    acceptance_band: tuple[float, float] | None = None
    margin_floor: float
    suggested_price_range: tuple[float, float] | None = None
    evidence_quality: EvidenceQuality
    core_sample_count: int = Field(ge=0)
    excluded_sample_count: int = Field(ge=0)
    override_applied: bool = False
    override_evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ParsedPriceConfirmation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ConfirmationAction
    selected_price: float | None = Field(default=None, gt=0)
    evidence: tuple[str, ...] = ()


def assess_market_price_position(
    source: MarketPriceAssessmentInput,
) -> MarketPriceAssessment:
    """Assess target price using only the v53 core reference evidence."""
    threshold = THRESHOLDS[source.pricing_profile]
    margin_floor = _round_money(source.cost / (1 - source.min_margin_rate))
    quality = _evidence_quality(source)
    warnings: list[str] = []
    if quality == "low":
        warnings.append("market_evidence_not_strong_enough_for_hard_gate")

    if source.core_reference_price is None:
        return _assessment(
            source,
            status="unavailable",
            position="unavailable",
            reason_code="core_reference_price_unavailable",
            threshold=threshold,
            margin_floor=margin_floor,
            quality="unavailable",
            warnings=("core_market_reference_unavailable",),
        )

    reference = source.core_reference_price
    low = _round_money(reference * (1 - threshold))
    high = _round_money(reference * (1 + threshold))
    acceptance = (low, high)
    deviation = round((source.target_price - reference) / reference, 6)
    suggested_low = max(low, margin_floor)
    suggested = (
        _friendly_lower(suggested_low),
        _friendly_upper(high),
    )
    if suggested[0] > suggested[1]:
        suggested = None

    if source.market_mode == "advisory_only" or quality == "low":
        return _assessment(
            source,
            status="advisory_only",
            position=_position(source.target_price, low, high),
            reason_code="market_evidence_advisory_only",
            threshold=threshold,
            margin_floor=margin_floor,
            quality=quality,
            deviation=deviation,
            acceptance=acceptance,
            suggested=suggested,
            warnings=tuple(warnings),
        )

    if suggested is None:
        return _assessment(
            source,
            status="confirmation_required",
            position="cost_market_conflict",
            reason_code="cost_margin_conflicts_with_market_band",
            threshold=threshold,
            margin_floor=margin_floor,
            quality=quality,
            deviation=deviation,
            acceptance=acceptance,
            warnings=("no_feasible_price_inside_market_and_margin_constraints",),
        )

    position = _position(source.target_price, low, high)
    if position == "within_market":
        status: AssessmentStatus = "passed"
        reason = "target_price_within_market_band"
    elif source.pricing_override:
        status = "passed"
        reason = "pricing_override_accepted"
    else:
        status = "confirmation_required"
        reason = (
            "target_price_above_market_band"
            if position == "above_market"
            else "target_price_below_market_band"
        )
    return _assessment(
        source,
        status=status,
        position=position,
        reason_code=reason,
        threshold=threshold,
        margin_floor=margin_floor,
        quality=quality,
        deviation=deviation,
        acceptance=acceptance,
        suggested=suggested,
        override_applied=source.pricing_override and status == "passed",
        warnings=tuple(warnings),
    )


def parse_price_confirmation(
    message: str, assessment: MarketPriceAssessment
) -> ParsedPriceConfirmation:
    text = message.strip()
    if any(token in text for token in ("只看市场", "只查看市场", "仅查看市场", "不要上架")):
        return ParsedPriceConfirmation(action="market_analysis_only")
    if any(token in text for token in ("保留原价", "维持原价", "继续用原价")):
        evidence = _extract_evidence(text)
        return ParsedPriceConfirmation(
            action="keep_original_with_evidence" if evidence else "unknown",
            evidence=(evidence,) if evidence else (),
        )
    if any(token in text for token in ("采用建议", "使用建议", "调整售价", "改成")):
        values = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*元?", text)]
        if values:
            selected = values[-1]
        elif assessment.suggested_price_range:
            low, high = assessment.suggested_price_range
            selected = _friendly_nearest((low + high) / 2)
        else:
            return ParsedPriceConfirmation(action="unknown")
        return ParsedPriceConfirmation(
            action="adopt_suggested_price", selected_price=selected
        )
    return ParsedPriceConfirmation(action="unknown")


def _assessment(
    source: MarketPriceAssessmentInput,
    *,
    status: AssessmentStatus,
    position: PricePosition,
    reason_code: str,
    threshold: float,
    margin_floor: float,
    quality: EvidenceQuality,
    deviation: float | None = None,
    acceptance: tuple[float, float] | None = None,
    suggested: tuple[float, float] | None = None,
    override_applied: bool = False,
    warnings: tuple[str, ...] = (),
) -> MarketPriceAssessment:
    return MarketPriceAssessment(
        status=status,
        position=position,
        reason_code=reason_code,
        target_price=source.target_price,
        cost=source.cost,
        min_margin_rate=source.min_margin_rate,
        pricing_profile=source.pricing_profile,
        threshold_rate=threshold,
        core_reference_price=source.core_reference_price,
        reference_method=source.reference_method,
        core_mean_price=source.core_mean_price,
        core_median_price=source.core_median_price,
        core_price_band=source.core_price_band,
        full_market_band=source.full_market_band,
        deviation_rate=deviation,
        acceptance_band=acceptance,
        margin_floor=margin_floor,
        suggested_price_range=suggested,
        evidence_quality=quality,
        core_sample_count=source.core_sample_count,
        excluded_sample_count=source.excluded_sample_count,
        override_applied=override_applied,
        override_evidence=(
            source.pricing_override_evidence if override_applied else ()
        ),
        warnings=warnings,
    )


def _evidence_quality(source: MarketPriceAssessmentInput) -> EvidenceQuality:
    if source.core_reference_price is None:
        return "unavailable"
    if (
        source.core_sample_count >= MARKET_PRICE_HIGH_QUALITY_MIN_SAMPLES
        and source.distribution_status == "stable"
        and source.market_mode == "decision_ready"
    ):
        return "high"
    if (
        source.core_sample_count >= MARKET_PRICE_MEDIUM_QUALITY_MIN_SAMPLES
        and source.distribution_status not in {"multimodal", "insufficient"}
        and source.market_mode == "decision_ready"
    ):
        return "medium"
    return "low"


def _position(price: float, low: float, high: float) -> PricePosition:
    if price < low:
        return "below_market"
    if price > high:
        return "above_market"
    return "within_market"


def _round_money(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def _friendly_lower(value: float) -> float:
    candidate = math.ceil((value - 9) / 10) * 10 + 9
    return float(candidate if candidate >= value else math.ceil(value))


def _friendly_upper(value: float) -> float:
    candidate = math.floor((value - 9) / 10) * 10 + 9
    return float(candidate if candidate <= value else math.floor(value))


def _friendly_nearest(value: float) -> float:
    return float(round((value - 9) / 10) * 10 + 9)


def _extract_evidence(text: str) -> str:
    match = re.search(r"(?:因为|依据是|理由是|定位是|优势是)\s*[：:]?\s*(.+)", text)
    if not match:
        return ""
    evidence = match.group(1).strip(" ，,。;；")
    return evidence if len(evidence) >= 4 else ""
