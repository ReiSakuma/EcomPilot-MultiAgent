from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SourceType = Literal["synthetic_demo", "imported_file", "platform_api"]


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    label: str
    comparison_mode: Literal["none", "previous_period", "campaign_window"] = "none"


class DailyProductMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    product_id: str
    metric_date: date
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    orders: int = Field(ge=0)
    units_sold: int = Field(ge=0)
    revenue: float = Field(ge=0)
    refunds: int = Field(ge=0)
    ending_inventory: int = Field(ge=0)
    source_type: SourceType
    source_updated_at: datetime


class CampaignMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    campaign_id: str
    product_id: str
    campaign_name: str
    start_date: date
    end_date: date
    discount: float = Field(ge=0)
    spend: float = Field(ge=0)
    units_sold: int = Field(ge=0)
    revenue: float = Field(ge=0)
    roi: float
    source_type: SourceType
    source_updated_at: datetime


class InventoryMovement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movement_id: str
    tenant_id: str
    product_id: str
    movement_date: date
    movement_type: Literal["initial", "sale", "refund", "adjustment", "restock"]
    quantity_delta: int
    ending_inventory: int = Field(ge=0)
    source_type: SourceType
    source_updated_at: datetime
