from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


PROMOTION_PROTOCOL_VERSION = "1.0"


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListingModelOutput(ModelOutput):
    title: str = Field(min_length=1, max_length=120)
    keywords: list[str] = Field(min_length=1, max_length=8)
    bullets: list[str] = Field(min_length=1, max_length=6)
    compliance_notes: list[str] = Field(max_length=8)

    @field_validator("title")
    @classmethod
    def normalize_non_blank_title(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


class PromotionBase(ModelOutput):
    """Canonical promotion contract shared by models, tools and artifacts."""

    protocol_version: Literal["1.0"] = PROMOTION_PROTOCOL_VERSION
    currency: Literal["CNY"] = "CNY"


class NoPromotionSpec(PromotionBase):
    promotion_type: Literal["none"] = "none"


class FixedAmountCouponSpec(PromotionBase):
    promotion_type: Literal["fixed_amount_coupon"] = "fixed_amount_coupon"
    discount_amount_yuan: float = Field(gt=0, le=1_000_000)


class PercentageDiscountSpec(PromotionBase):
    promotion_type: Literal["percentage_discount"] = "percentage_discount"
    # 10% off is 0.10; Chinese "九折" therefore means 0.10 off / 0.90 pay rate.
    discount_rate: float = Field(gt=0, lt=1)


class GiftPromotionSpec(PromotionBase):
    promotion_type: Literal["gift"] = "gift"
    gift_name: str = Field(min_length=1, max_length=120)
    gift_quantity: int = Field(default=1, ge=1, le=100)


class BundlePromotionSpec(PromotionBase):
    promotion_type: Literal["bundle"] = "bundle"
    bundle_quantity: int = Field(ge=2, le=100)
    bundle_price_yuan: float = Field(gt=0, le=10_000_000)


PromotionSpec = Annotated[
    Union[
        NoPromotionSpec,
        FixedAmountCouponSpec,
        PercentageDiscountSpec,
        GiftPromotionSpec,
        BundlePromotionSpec,
    ],
    Field(discriminator="promotion_type"),
]
PROMOTION_ADAPTER = TypeAdapter(PromotionSpec)


def validate_promotion(value: Any) -> PromotionSpec:
    return PROMOTION_ADAPTER.validate_python(value)


def promotion_discount_amount_yuan(promotion: PromotionSpec, price: float) -> float:
    """Convert a typed promotion to the governed per-item price reduction."""

    if isinstance(promotion, FixedAmountCouponSpec):
        return round(promotion.discount_amount_yuan, 2)
    if isinstance(promotion, PercentageDiscountSpec):
        return round(price * promotion.discount_rate, 2)
    if isinstance(promotion, BundlePromotionSpec):
        regular_total = price * promotion.bundle_quantity
        return round(max(0.0, regular_total - promotion.bundle_price_yuan), 2)
    return 0.0


class StrategyModelOutput(ModelOutput):
    launch_plan: str = Field(min_length=5, max_length=800)
    rationale: str = Field(min_length=3, max_length=500)
    promotion: PromotionSpec | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_known_v55_output(cls, value: Any):
        """Read the documented v55 amount-in-CNY field without exposing it in v56 Schema."""

        if not isinstance(value, dict) or "selected_discount" not in value:
            return value
        payload = dict(value)
        legacy_amount = payload.pop("selected_discount")
        if payload.get("promotion") is None and legacy_amount is not None:
            amount = float(legacy_amount)
            payload["promotion"] = (
                {"promotion_type": "none"}
                if amount == 0
                else {
                    "promotion_type": "fixed_amount_coupon",
                    "discount_amount_yuan": amount,
                }
            )
        return payload


class CoreStrategyProposal(ModelOutput):
    """Small interview-core contract: the model proposes, tools own the numbers."""

    launch_plan: str = Field(min_length=5, max_length=500)
    rationale: str = Field(min_length=3, max_length=300)
    discount_amount_yuan: float = Field(default=0, ge=0, le=1_000_000)


class CoreReviewIssue(ModelOutput):
    code: Literal[
        "unsupported_product_claim",
        "prohibited_marketing_claim",
        "execution_risk",
    ]
    field_path: Literal[
        "listing.title",
        "listing.keywords",
        "listing.bullets",
        "strategy.launch_plan",
        "strategy.strategy_rationale",
    ]
    claim_text: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=3, max_length=80)


class CoreReviewOutput(ModelOutput):
    """Semantic-only review. Numeric checks remain deterministic."""

    issues: list[CoreReviewIssue] = Field(default_factory=list, max_length=3)


class MarketResearchModelOutput(ModelOutput):
    insight_summary: str = Field(min_length=5, max_length=500)
    query_rationale: str = Field(min_length=3, max_length=300)
    recommended_product_ids: list[str] = Field(default_factory=list, max_length=8)


class AnalyticsModelOutput(ModelOutput):
    """Narrative-only output; business numbers always come from governed tools."""

    summary: str = Field(min_length=5, max_length=600)
    selected_evidence_tools: list[Literal[
        "get_sales_metrics",
        "compare_sales_periods",
        "get_campaign_performance",
        "get_inventory_history",
    ]] = Field(default_factory=list, max_length=4)
    caveats: list[str] = Field(default_factory=list, max_length=5)


class ReviewFinding(ModelOutput):
    code: Literal[
        "no_blocking_issue",
        "unsupported_product_claim",
        "discount_representation_mismatch",
        "margin_inconsistency",
        "inventory_inconsistency",
        "prohibited_marketing_claim",
        "execution_risk",
    ]
    severity: Literal["low", "medium", "high"]
    blocking: bool
    message: str = Field(min_length=3, max_length=80)
    source_agent: Literal["listing_agent", "strategy_agent"] | None = None
    artifact_type: Literal["listing", "strategy"] | None = None
    field_path: Literal[
        "listing.title",
        "listing.keywords",
        "listing.bullets",
        "strategy.launch_plan",
        "strategy.strategy_rationale",
        "strategy.price",
        "strategy.coupon",
        "strategy.margin",
        "strategy.inventory_check",
        "strategy.planned_units",
    ] | None = None
    claim_text: str | None = Field(default=None, min_length=1, max_length=80)
    suggested_action: Literal[
        "remove_unconfirmed_claim",
        "rewrite_claim",
        "fix_discount_representation",
        "recalculate_strategy",
        "reduce_planned_units",
        "stop_execution",
    ] | None = None

    @model_validator(mode="after")
    def blocking_requires_high_severity(self):
        if self.blocking and self.severity != "high":
            raise ValueError("blocking findings must have severity='high'")
        if self.code == "no_blocking_issue" and (
            self.blocking or self.severity != "low"
        ):
            raise ValueError("no_blocking_issue must be low severity and non-blocking")
        if self.blocking and any(
            value is None
            for value in (
                self.source_agent,
                self.artifact_type,
                self.field_path,
                self.suggested_action,
            )
        ):
            raise ValueError("blocking findings must identify a revision source and field")
        if self.source_agent == "listing_agent" and (
            self.artifact_type != "listing"
            or not str(self.field_path).startswith("listing.")
        ):
            raise ValueError("listing_agent findings must target a listing field")
        if self.source_agent == "strategy_agent" and (
            self.artifact_type != "strategy"
            or not str(self.field_path).startswith("strategy.")
        ):
            raise ValueError("strategy_agent findings must target a strategy field")
        if self.code in {
            "unsupported_product_claim",
            "prohibited_marketing_claim",
            "discount_representation_mismatch",
        } and self.blocking and not self.claim_text:
            raise ValueError("blocking content findings must include claim_text")
        return self


class ReviewModelOutput(ModelOutput):
    findings: list[ReviewFinding] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def passing_finding_must_stand_alone(self):
        if any(item.code == "no_blocking_issue" for item in self.findings) and len(
            self.findings
        ) != 1:
            raise ValueError("no_blocking_issue must be the only finding")
        return self
