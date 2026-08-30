from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.model.contracts import (
    PROMOTION_PROTOCOL_VERSION,
    PromotionSpec,
    validate_promotion,
)


class PromotionMigrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "migrated", "requires_regeneration", "not_applicable"]
    source_field: str | None = None
    source_unit: str | None = None
    target_protocol_version: Literal["1.0"] = PROMOTION_PROTOCOL_VERSION
    promotion: PromotionSpec | None = None
    reason_code: str


def migrate_legacy_promotion(payload: dict[str, Any]) -> PromotionMigrationResult:
    """Interpret documented legacy units and reject genuinely ambiguous values."""

    if payload.get("promotion") is not None:
        return PromotionMigrationResult(
            status="current",
            source_field="promotion",
            source_unit="typed",
            promotion=validate_promotion(payload["promotion"]),
            reason_code="already_typed",
        )

    if "coupon" in payload:
        return _amount_result(payload.get("coupon"), "coupon", "legacy_coupon_cny")
    if "discount_amount_yuan" in payload:
        return _amount_result(
            payload.get("discount_amount_yuan"),
            "discount_amount_yuan",
            "explicit_cny_field",
        )

    for field in ("discount", "selected_discount"):
        if field not in payload:
            continue
        unit = str(payload.get(f"{field}_unit") or payload.get("discount_unit") or "").lower()
        if unit in {"yuan", "cny", "rmb", "amount_yuan"}:
            return _amount_result(payload[field], field, unit)
        if unit in {"rate", "ratio", "percent", "percentage"}:
            rate = float(payload[field])
            if unit in {"percent", "percentage"}:
                rate /= 100
            return PromotionMigrationResult(
                status="migrated",
                source_field=field,
                source_unit=unit,
                promotion=validate_promotion(
                    {"promotion_type": "percentage_discount", "discount_rate": rate}
                ),
                reason_code="explicit_percentage_unit",
            )
        return PromotionMigrationResult(
            status="requires_regeneration",
            source_field=field,
            reason_code="ambiguous_legacy_discount_unit",
        )

    return PromotionMigrationResult(
        status="not_applicable",
        promotion=validate_promotion({"promotion_type": "none"}),
        reason_code="no_legacy_promotion_fields",
    )


def migrate_checkpoint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return an in-memory migrated copy; the checkpoint file is never rewritten."""

    migrated = deepcopy(payload)
    strategy = (migrated.get("agent_outputs") or {}).get("strategy_agent")
    reports: list[dict[str, Any]] = [
        item
        for item in (migrated.get("protocol_migrations") or [])
        if item.get("protocol") != "promotion_spec"
    ]
    if isinstance(strategy, dict):
        result = migrate_legacy_promotion(strategy)
        if result.status == "migrated":
            strategy["promotion_migration"] = result.model_dump(mode="json")
        if result.promotion is not None:
            strategy["promotion"] = result.promotion.model_dump(mode="json")
            strategy["promotion_protocol_version"] = PROMOTION_PROTOCOL_VERSION
        reports.append(
            {
                "protocol": "promotion_spec",
                **result.model_dump(mode="json", exclude={"promotion"}),
            }
        )

    artifacts = migrated.get("artifacts") or {}
    for artifact in artifacts.values():
        if not isinstance(artifact, dict) or artifact.get("artifact_type") != "strategy":
            continue
        result = migrate_legacy_promotion(artifact)
        if result.status == "migrated" and result.promotion is not None:
            artifact["promotion_migration"] = result.model_dump(mode="json")
            artifact["promotion"] = result.promotion.model_dump(mode="json")
            artifact["promotion_protocol_version"] = PROMOTION_PROTOCOL_VERSION
            artifact["content_hash"] = ""

    migrated["protocol_migrations"] = reports
    return migrated


def _amount_result(value: Any, source_field: str, source_unit: str) -> PromotionMigrationResult:
    amount = float(value or 0)
    promotion = (
        validate_promotion({"promotion_type": "none"})
        if amount == 0
        else validate_promotion(
            {
                "promotion_type": "fixed_amount_coupon",
                "discount_amount_yuan": amount,
            }
        )
    )
    return PromotionMigrationResult(
        status="migrated",
        source_field=source_field,
        source_unit=source_unit,
        promotion=promotion,
        reason_code="explicit_fixed_amount_unit",
    )
