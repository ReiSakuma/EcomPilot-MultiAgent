from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProductDraft(BaseModel):
    product_id: str
    title: str
    price: float
    stock: int
    bullets: list[str] = Field(default_factory=list)
    coupon: float = 0.0
    status: Literal["draft", "published"] = "draft"


class Promotion(BaseModel):
    promotion_id: str
    product_id: str
    coupon: float
    status: Literal["draft", "active"] = "draft"


class SellerCenterSnapshot(BaseModel):
    products: dict[str, ProductDraft] = Field(default_factory=dict)
    promotions: dict[str, Promotion] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    operation: Literal["update_listing", "create_coupon", "publish_listing"]
    product_id: str
    title: str | None = None
    bullets: list[str] = Field(default_factory=list)
    price: float | None = None
    stock: int | None = None
    coupon: float = 0.0
    task_id: str | None = None
    run_id: str | None = None
    checkpoint_version: int | None = Field(default=None, ge=0)
    source_artifact_hashes: dict[str, str] = Field(default_factory=dict)
    payload_hash: str = ""

    @model_validator(mode="after")
    def verify_payload_hash(self):
        expected = execution_plan_payload_hash(self)
        if self.payload_hash and self.payload_hash != expected:
            raise ValueError("ExecutionPlan payload_hash does not match its business payload")
        if not self.payload_hash:
            object.__setattr__(self, "payload_hash", expected)
        return self


def execution_plan_payload_hash(plan: ExecutionPlan | dict) -> str:
    payload = (
        plan.model_dump(mode="json", exclude={"payload_hash"})
        if isinstance(plan, ExecutionPlan)
        else {key: value for key, value in dict(plan).items() if key != "payload_hash"}
    )
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExecutionVerification(BaseModel):
    verified: bool
    checks: dict[str, bool]
    observed: dict[str, object]
    errors: list[str] = Field(default_factory=list)
