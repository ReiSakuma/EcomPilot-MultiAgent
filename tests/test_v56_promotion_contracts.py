from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.model.contracts import (
    PROMOTION_ADAPTER,
    FixedAmountCouponSpec,
    PercentageDiscountSpec,
    StrategyModelOutput,
    promotion_discount_amount_yuan,
    validate_promotion,
)
from app.model.promotion_migration import (
    migrate_checkpoint_payload,
    migrate_legacy_promotion,
)
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.workflow import run_workflow
from app.observability.store import TraceStore
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本95元、售价199元、库存800件的无线耳机，"
    "主要面向大学生，毛利率不低于25%。"
)


def test_v56_fixed_amount_percentage_and_nine_tenths_are_unambiguous() -> None:
    fixed = validate_promotion(
        {"promotion_type": "fixed_amount_coupon", "discount_amount_yuan": 10}
    )
    ten_percent_off = validate_promotion(
        {"promotion_type": "percentage_discount", "discount_rate": 0.10}
    )
    nine_tenths_pay = PercentageDiscountSpec(discount_rate=1 - 0.90)

    assert isinstance(fixed, FixedAmountCouponSpec)
    assert promotion_discount_amount_yuan(fixed, 300) == 10
    assert promotion_discount_amount_yuan(ten_percent_off, 300) == 30
    assert promotion_discount_amount_yuan(nine_tenths_pay, 300) == 30
    assert nine_tenths_pay.discount_rate != 0.90


def test_v56_each_promotion_type_accepts_only_its_own_fields() -> None:
    with pytest.raises(ValidationError):
        validate_promotion(
            {
                "promotion_type": "fixed_amount_coupon",
                "discount_amount_yuan": 10,
                "discount_rate": 0.10,
            }
        )
    with pytest.raises(ValidationError):
        validate_promotion(
            {"promotion_type": "percentage_discount", "discount_amount_yuan": 10}
        )


@pytest.mark.parametrize(
    ("payload", "promotion_type", "expected"),
    [
        (
            {"discount": 10, "discount_unit": "yuan"},
            "fixed_amount_coupon",
            10,
        ),
        (
            {"discount": 10, "discount_unit": "percent"},
            "percentage_discount",
            0.10,
        ),
        ({"coupon": 10}, "fixed_amount_coupon", 10),
    ],
)
def test_v56_explicit_legacy_units_are_migrated(
    payload: dict, promotion_type: str, expected: float
) -> None:
    result = migrate_legacy_promotion(payload)

    assert result.status == "migrated"
    assert result.promotion is not None
    assert result.promotion.promotion_type == promotion_type
    value = getattr(
        result.promotion,
        "discount_amount_yuan",
        getattr(result.promotion, "discount_rate", None),
    )
    assert value == expected


def test_v56_ambiguous_legacy_value_requires_regeneration() -> None:
    result = migrate_legacy_promotion({"discount": 10})

    assert result.status == "requires_regeneration"
    assert result.promotion is None
    assert result.reason_code == "ambiguous_legacy_discount_unit"


def test_v56_round_trip_preserves_type_amount_rate_currency_and_protocol() -> None:
    values = [
        validate_promotion(
            {"promotion_type": "fixed_amount_coupon", "discount_amount_yuan": 10}
        ),
        validate_promotion(
            {"promotion_type": "percentage_discount", "discount_rate": 0.10}
        ),
        validate_promotion(
            {"promotion_type": "gift", "gift_name": "耳机收纳袋", "gift_quantity": 1}
        ),
        validate_promotion(
            {"promotion_type": "bundle", "bundle_quantity": 2, "bundle_price_yuan": 499}
        ),
    ]

    for original in values:
        restored = PROMOTION_ADAPTER.validate_json(
            PROMOTION_ADAPTER.dump_json(original)
        )
        assert restored.model_dump(mode="json") == original.model_dump(mode="json")
        assert restored.protocol_version == "1.0"
        assert restored.currency == "CNY"


def test_v56_model_schema_exposes_typed_promotion_not_raw_selected_discount() -> None:
    schema = json.dumps(StrategyModelOutput.model_json_schema(), ensure_ascii=False)

    assert "promotion_type" in schema
    assert "discount_amount_yuan" in schema
    assert "selected_discount" not in schema


def test_v56_tool_schemas_and_results_use_canonical_yuan_fields() -> None:
    registry = ToolRegistry()
    margin_schema = registry.input_model("calculate_margin").model_json_schema()

    assert "discount_amount_yuan" in margin_schema["properties"]
    assert "discount" not in margin_schema["properties"]
    assert "simulate_discount_scenarios" not in registry.specs()
    with registry.agent_scope("strategy_agent", task_id="task_v56_tools"):
        result = registry.call(
            "calculate_margin", price=199, cost=95, discount_amount_yuan=10
        )
    assert result["discount_amount_yuan"] == 10
    assert result["promotion_protocol_version"] == "1.0"


def test_v56_checkpoint_migration_is_read_only_and_auditable(tmp_path: Path) -> None:
    state = run_workflow(GOAL, approved=False)
    raw = state.model_dump(mode="json")
    strategy = raw["agent_outputs"]["strategy_agent"]
    strategy.pop("promotion")
    strategy.pop("promotion_protocol_version")
    for artifact in raw["artifacts"].values():
        if artifact["artifact_type"] == "strategy":
            artifact.pop("promotion")
            artifact.pop("promotion_protocol_version")
            artifact.pop("promotion_migration", None)
    path = tmp_path / f"{state.task_id}.json"
    source_text = json.dumps(raw, ensure_ascii=False, indent=2)
    path.write_text(source_text, encoding="utf-8")

    restored = CheckpointStore(tmp_path).load(state.task_id)

    assert path.read_text(encoding="utf-8") == source_text
    assert restored.agent_outputs["strategy_agent"]["promotion"][
        "promotion_type"
    ] == "fixed_amount_coupon"
    assert restored.protocol_migrations[-1]["status"] == "migrated"
    artifact = restored.artifacts[restored.latest_artifacts["strategy_agent"]]
    assert artifact.promotion_protocol_version == "1.0"
    assert artifact.promotion_migration is not None


def test_v56_ambiguous_checkpoint_gets_concrete_migration_state() -> None:
    migrated = migrate_checkpoint_payload(
        {
            "goal": "legacy",
            "agent_outputs": {"strategy_agent": {"discount": 10}},
        }
    )

    report = migrated["protocol_migrations"][-1]
    assert report["status"] == "requires_regeneration"
    assert report["reason_code"] == "ambiguous_legacy_discount_unit"
    assert "promotion" not in migrated["agent_outputs"]["strategy_agent"]


def test_v56_strategy_artifact_trace_payload_and_run_bundle_declare_protocol() -> None:
    state = run_workflow(GOAL, approved=False)
    strategy = state.agent_outputs["strategy_agent"]
    artifact = state.artifacts[state.latest_artifacts["strategy_agent"]]

    assert strategy["promotion_protocol_version"] == "1.0"
    assert artifact.promotion_protocol_version == "1.0"
    assert strategy["promotion"]["currency"] == "CNY"
    assert strategy["coupon"] == strategy["margin"]["discount_amount_yuan"]
    trace = TraceStore().get_run(state.run_id)
    strategy_events = [
        event
        for event in trace["events"]
        if event.get("component_name") == "strategy_agent"
        and event.get("event_type") == "agent_completed"
    ]
    assert strategy_events[-1]["details"]["artifact"][
        "promotion_protocol_version"
    ] == "1.0"
    exporter = (Path(__file__).resolve().parents[1] / "scripts/export_run_bundle.py").read_text(
        encoding="utf-8"
    )
    assert '"promotion_protocol_version": PROMOTION_PROTOCOL_VERSION' in exporter
