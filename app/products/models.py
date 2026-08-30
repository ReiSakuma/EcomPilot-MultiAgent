from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    product_id: str
    sku: str | None = None
    title: str
    category: str
    status: Literal["draft", "published", "deleted"]
    source_task_id: str
    seller_snapshot: dict[str, Any] = Field(default_factory=dict)
    resource_version: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class TaskProductLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    task_id: str
    product_id: str
    conversation_id: str | None = None
    relation: Literal["created", "modified", "referenced"]
    artifact_refs: list[str] = Field(default_factory=list)
    linked_at: datetime


class ProductEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    tenant_id: str
    product_id: str
    task_id: str | None = None
    conversation_id: str | None = None
    event_type: Literal[
        "listing_created",
        "listing_revised",
        "reviewed",
        "store_synced",
        "published",
        "promotion_activated",
    ]
    status: Literal["completed", "failed", "pending"]
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    occurred_at: datetime


class ProductCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    sku: str | None = None
    title: str
    category: str
    status: Literal["draft", "published"]
    source_task_id: str


class EntityResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["resolved", "ambiguous", "not_found"]
    query: str
    strategy: Literal[
        "product_id",
        "sku",
        "task_link",
        "exact_title",
        "conversation_active",
        "recent_reference",
        "fuzzy_candidates",
        "none",
    ]
    product_id: str | None = None
    candidates: list[ProductCandidate] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    explanation: str


class ProductDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: ProductRecord
    task_links: list[TaskProductLink] = Field(default_factory=list)
    timeline: list[ProductEvent] = Field(default_factory=list)
    seller_state_available: bool
