from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.model.contracts import PROMOTION_PROTOCOL_VERSION, PromotionSpec
from app.model.promotion_migration import (
    PromotionMigrationResult,
    migrate_legacy_promotion,
)


ARTIFACT_SCHEMA_VERSION = "1.0"
ARTIFACT_METADATA_FIELDS = {
    "artifact_id",
    "artifact_type",
    "schema_version",
    "task_id",
    "producer",
    "input_state_version",
    "confidence",
    "evidence_refs",
    "created_at",
    "content_hash",
}


class ArtifactBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(default_factory=lambda: f"artifact_{uuid4().hex[:12]}")
    artifact_type: str
    schema_version: Literal["1.0"] = ARTIFACT_SCHEMA_VERSION
    task_id: str
    producer: str
    input_state_version: int = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence_refs: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str = ""

    @model_validator(mode="after")
    def assign_content_hash(self):
        if self.content_hash:
            return self
        canonical = json.dumps(
            {
                "artifact_type": self.artifact_type,
                "schema_version": self.schema_version,
                "task_id": self.task_id,
                "producer": self.producer,
                "input_state_version": self.input_state_version,
                "confidence": self.confidence,
                "evidence_refs": self.evidence_refs,
                "data": self.business_payload(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        object.__setattr__(
            self,
            "content_hash",
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        return self

    def business_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude=ARTIFACT_METADATA_FIELDS)

    def legacy_result(self) -> dict[str, Any]:
        return self.business_payload()


class ResearchEvidence(ArtifactBase):
    artifact_type: Literal["research_evidence"] = "research_evidence"
    sample_size: dict[str, int]
    price_band: tuple[float, float]
    median_price: float
    mean_price: float = 0
    raw_price_band: tuple[float, float] = (0, 0)
    market_statistics: dict[str, Any] = Field(default_factory=dict)
    market_layers: dict[str, Any] = Field(default_factory=dict)
    core_reference_price: float | None = None
    reference_method: str = "unavailable"
    full_market_band: tuple[float, float] = (0, 0)
    top_features: tuple[str, ...]
    feature_counts: dict[str, int]
    pain_points: tuple[str, ...]
    pain_point_counts: dict[str, int]
    pain_point_evidence: dict[str, tuple[str, ...]]
    competitors: tuple[dict[str, Any], ...]
    keywords: tuple[str, ...]
    research_mode: str = "deterministic"
    evidence_status: Literal["baseline", "enhanced", "degraded"] = "baseline"
    degradation: dict[str, Any] | None = None
    sql_research: dict[str, Any] | None = None
    react_context_budget: dict[str, Any] | None = None

    def legacy_result(self) -> dict[str, Any]:
        result = self.business_payload()
        if result.get("react_context_budget") is None:
            result.pop("react_context_budget", None)
        return {**result, "evidence_refs": list(self.evidence_refs)}


class MarketPriceAssessmentArtifact(ArtifactBase):
    artifact_type: Literal["market_price_assessment"] = "market_price_assessment"
    policy_version: Literal["market-price-gate-v1"] = "market-price-gate-v1"
    status: Literal["passed", "confirmation_required", "advisory_only", "unavailable"]
    position: Literal[
        "below_market", "within_market", "above_market", "cost_market_conflict", "unavailable"
    ]
    reason_code: str
    target_price: float
    cost: float
    min_margin_rate: float
    pricing_profile: Literal["commodity", "standard", "differentiated"]
    threshold_rate: float
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
    evidence_quality: Literal["high", "medium", "low", "unavailable"]
    core_sample_count: int
    excluded_sample_count: int
    override_applied: bool = False
    override_evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ListingArtifact(ArtifactBase):
    artifact_type: Literal["listing"] = "listing"
    title: str
    keywords: tuple[str, ...]
    bullets: tuple[str, ...]
    compliance_notes: tuple[str, ...]
    generation_mode: str
    market_evidence_summary: dict[str, Any] | None = None
    revision_iteration: int = 0
    revision_applied_findings: tuple[dict[str, Any], ...] = ()
    semantic_corrections: tuple[dict[str, Any], ...] = ()
    content_normalization_version: str | None = None

    def legacy_result(self) -> dict[str, Any]:
        result = self.business_payload()
        if result.get("market_evidence_summary") is None:
            result.pop("market_evidence_summary", None)
        if result.get("content_normalization_version") is None:
            result.pop("content_normalization_version", None)
        return result


class StrategyArtifact(ArtifactBase):
    artifact_type: Literal["strategy"] = "strategy"
    price: float
    promotion_protocol_version: Literal["1.0"] = PROMOTION_PROTOCOL_VERSION
    promotion: PromotionSpec
    promotion_migration: PromotionMigrationResult | None = None
    # Compatibility projection for Seller Center v1; models do not own this field.
    coupon: float
    launch_plan: str
    planned_units: int
    margin: dict[str, Any]
    inventory_check: dict[str, Any]
    strategy_rationale: str
    generation_mode: str
    market_price_reference: dict[str, Any]
    selected_evidence_tools: tuple[str, ...] = ()
    decision_evidence: dict[str, Any] = Field(default_factory=dict)
    revision_iteration: int = 0
    revision_applied_findings: tuple[dict[str, Any], ...] = ()
    semantic_corrections: tuple[dict[str, Any], ...] = ()
    core_protocol_version: Literal["interview-core-strategy-v1"] = (
        "interview-core-strategy-v1"
    )
    proposal_audit: dict[str, Any] = Field(default_factory=dict)
    strategy_render_version: str | None = None
    numeric_ownership: dict[str, Any] = Field(default_factory=dict)
    render_manifest: dict[str, Any] = Field(default_factory=dict)
    react_context_budget: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_v55_coupon(cls, value):
        if not isinstance(value, dict) or value.get("promotion") is not None:
            return value
        payload = dict(value)
        migration = migrate_legacy_promotion(payload)
        if migration.promotion is None:
            raise ValueError(
                "Legacy Strategy Artifact promotion is ambiguous; regenerate Strategy"
            )
        payload["promotion"] = migration.promotion.model_dump(mode="json")
        payload["promotion_protocol_version"] = PROMOTION_PROTOCOL_VERSION
        payload["promotion_migration"] = migration.model_dump(mode="json")
        if payload.get("content_hash"):
            payload["content_hash"] = ""
        return payload

    def legacy_result(self) -> dict[str, Any]:
        result = self.business_payload()
        if result.get("promotion_migration") is None:
            result.pop("promotion_migration", None)
        if not result.get("revision_iteration"):
            result.pop("revision_iteration", None)
            result.pop("revision_applied_findings", None)
        if result.get("strategy_render_version") is None:
            result.pop("strategy_render_version", None)
            result.pop("numeric_ownership", None)
            result.pop("render_manifest", None)
        if result.get("react_context_budget") is None:
            result.pop("react_context_budget", None)
        return result


class RiskDecision(ArtifactBase):
    artifact_type: Literal["risk_decision"] = "risk_decision"
    approved_for_execution: bool
    violations: tuple[str, ...]
    review_notes: tuple[str, ...]
    review_findings: tuple[dict[str, Any], ...] = ()
    revision_requested: bool = False
    revision_target: str | None = None
    revision_targets: tuple[str, ...] = ()
    generation_mode: str
    execution_plan: dict[str, Any]
    consistency_checks: tuple[dict[str, Any], ...] = ()
    correction_audit: tuple[dict[str, Any], ...] = ()
    execution_manifest: dict[str, Any] = Field(default_factory=dict)

    def legacy_result(self) -> dict[str, Any]:
        result = self.business_payload()
        if not result.get("revision_targets"):
            result.pop("revision_targets", None)
        if not result.get("execution_manifest"):
            result.pop("execution_manifest", None)
        return result


class ExecutionReceipt(ArtifactBase):
    artifact_type: Literal["execution_receipt"] = "execution_receipt"
    risk: str
    execution_plan: dict[str, Any] | None = None
    browser_result: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None

    def legacy_result(self) -> dict[str, Any]:
        result = self.business_payload()
        return {key: value for key, value in result.items() if value is not None}


class AnalyticsArtifact(ArtifactBase):
    artifact_type: Literal["analytics_report"] = "analytics_report"
    product_id: str
    period: dict[str, Any]
    sales: dict[str, Any]
    comparison: dict[str, Any] | None = None
    campaigns: dict[str, Any] | None = None
    inventory: dict[str, Any] | None = None
    selected_evidence_tools: tuple[str, ...]
    narrative: str
    generation_mode: str
    source_type: str
    source_updated_at: str
    react_context_budget: dict[str, Any] | None = None

    def legacy_result(self) -> dict[str, Any]:
        result = self.business_payload()
        if result.get("react_context_budget") is None:
            result.pop("react_context_budget", None)
        return result


Artifact = Annotated[
    Union[
        ResearchEvidence,
        MarketPriceAssessmentArtifact,
        ListingArtifact,
        StrategyArtifact,
        RiskDecision,
        ExecutionReceipt,
        AnalyticsArtifact,
    ],
    Field(discriminator="artifact_type"),
]


ARTIFACT_MODEL_BY_AGENT: dict[str, type[ArtifactBase]] = {
    "market_agent": ResearchEvidence,
    "market_price_gate_agent": MarketPriceAssessmentArtifact,
    "listing_agent": ListingArtifact,
    "strategy_agent": StrategyArtifact,
    "review_agent": RiskDecision,
    "browser_agent": ExecutionReceipt,
    "analytics_agent": AnalyticsArtifact,
}


def artifact_from_result(
    *,
    task_id: str,
    producer: str,
    result: dict[str, Any],
    input_state_version: int,
    confidence: float,
    evidence_refs: list[str] | tuple[str, ...] = (),
) -> ArtifactBase:
    try:
        model = ARTIFACT_MODEL_BY_AGENT[producer]
    except KeyError as exc:
        raise ValueError(f"No artifact contract registered for agent '{producer}'") from exc

    payload = dict(result)
    if producer in {"market_agent", "analytics_agent"}:
        result_refs = payload.pop("evidence_refs", [])
        evidence_refs = tuple(evidence_refs or result_refs)
    return model.model_validate(
        {
            **payload,
            "task_id": task_id,
            "producer": producer,
            "input_state_version": input_state_version,
            "confidence": confidence,
            "evidence_refs": tuple(evidence_refs),
        }
    )
