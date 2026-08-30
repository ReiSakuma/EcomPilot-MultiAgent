from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.seller_center.schemas import ExecutionPlan


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductSearchInput(ToolInput):
    category: str = Field(min_length=1, max_length=80)
    target_audience: str | None = Field(default=None, max_length=80)


class KeywordSearchInput(ToolInput):
    category: str = Field(min_length=1, max_length=80)
    audience: str | None = Field(default=None, max_length=80)


class ReviewSearchInput(ToolInput):
    category: str = Field(min_length=1, max_length=80)
    product_ids: list[str] | None = None


class ReviewAnalysisInput(ToolInput):
    reviews: list[dict[str, Any]]


class FeatureAnalysisInput(ToolInput):
    products: list[dict[str, Any]]


class MarketReportInput(ToolInput):
    category: str = Field(min_length=1, max_length=80)
    target_audience: str | None = Field(default=None, max_length=80)
    confirmed_features: list[str] = Field(default_factory=list, max_length=30)
    confirmed_product_form: str | None = Field(default=None, max_length=80)
    channel: str = Field(default="general_ecommerce", min_length=1, max_length=80)
    condition: str = Field(default="new", min_length=1, max_length=40)
    brand_tier: str = Field(default="mass_market", min_length=1, max_length=40)


class SqlQueryInput(ToolInput):
    sql: str = Field(
        min_length=8,
        max_length=2000,
        description=(
            "One SQLite SELECT using only tables and columns from the supplied market schema. "
            "No writes, CTEs, subqueries, wildcards, PRAGMA, or multiple statements."
        ),
    )
    purpose: str = Field(
        default="market_research",
        min_length=3,
        max_length=120,
        description="Short business reason for this market-data query.",
    )


class MarginInput(ToolInput):
    price: float = Field(gt=0, le=1_000_000)
    cost: float = Field(ge=0, le=1_000_000)
    discount_amount_yuan: float = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_discount(cls, value):
        if isinstance(value, dict) and "discount" in value:
            payload = dict(value)
            if "discount_amount_yuan" in payload:
                raise ValueError("use only discount_amount_yuan")
            payload["discount_amount_yuan"] = payload.pop("discount")
            return payload
        return value

    @model_validator(mode="after")
    def discount_below_price(self):
        if self.discount_amount_yuan >= self.price:
            raise ValueError("discount_amount_yuan must be lower than price")
        return self


class DiscountInput(ToolInput):
    price: float = Field(gt=0, le=1_000_000)
    cost: float = Field(ge=0, le=1_000_000)
    min_margin_rate: float = Field(ge=0, lt=1)


class InventoryInput(ToolInput):
    inventory: int = Field(ge=0, le=100_000_000)
    planned_units: int = Field(ge=0, le=100_000_000)


class DemandForecastInput(ToolInput):
    category: str = Field(min_length=1, max_length=80)
    target_audience: str = Field(min_length=1, max_length=80)
    target_price: float = Field(gt=0, le=1_000_000)
    horizon_days: int = Field(default=30, ge=7, le=90)


class CampaignHistoryInput(ToolInput):
    category: str = Field(min_length=1, max_length=80)
    target_audience: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=5, ge=1, le=10)


class CompetitorPriceTrendInput(ToolInput):
    category: str = Field(min_length=1, max_length=80)
    lookback_days: int = Field(default=60, ge=14, le=180)


class BrowserExecuteInput(ToolInput):
    plan: ExecutionPlan
    idempotency_key: str = Field(min_length=8, max_length=200)


class BrowserVerifyInput(ToolInput):
    plan: ExecutionPlan


class EmptyInput(ToolInput):
    pass


class AnalyticsRangeInput(ToolInput):
    product_id: str = Field(pattern=r"^product_[A-Za-z0-9_-]+$", max_length=128)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid_range(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if (self.end_date - self.start_date).days > 365:
            raise ValueError("analytics range must not exceed 366 days")
        return self
