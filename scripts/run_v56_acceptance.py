from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.model.contracts import (
    StrategyModelOutput,
    promotion_discount_amount_yuan,
    validate_promotion,
)
from app.model.promotion_migration import migrate_legacy_promotion
from app.orchestration.workflow import run_workflow
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本95元、售价199元、库存800件的无线耳机，"
    "主要面向大学生，毛利率不低于25%。"
)


def main() -> int:
    fixed = validate_promotion(
        {"promotion_type": "fixed_amount_coupon", "discount_amount_yuan": 10}
    )
    percent = validate_promotion(
        {"promotion_type": "percentage_discount", "discount_rate": 0.10}
    )
    invalid_rejected = False
    try:
        validate_promotion(
            {
                "promotion_type": "fixed_amount_coupon",
                "discount_amount_yuan": 10,
                "discount_rate": 0.10,
            }
        )
    except ValidationError:
        invalid_rejected = True

    explicit = migrate_legacy_promotion(
        {"discount": 10, "discount_unit": "yuan"}
    )
    ambiguous = migrate_legacy_promotion({"discount": 10})
    registry = ToolRegistry()
    margin_schema = registry.input_model("calculate_margin").model_json_schema()
    strategy_schema = json.dumps(
        StrategyModelOutput.model_json_schema(), ensure_ascii=False
    )
    state = run_workflow(GOAL, approved=False)
    strategy = state.agent_outputs["strategy_agent"]
    artifact = state.artifacts[state.latest_artifacts["strategy_agent"]]

    checks = {
        "version_is_v56": PROJECT_VERSION == "0.56.0",
        "fixed_10_yuan_is_10": promotion_discount_amount_yuan(fixed, 300) == 10,
        "ten_percent_is_30_at_300": (
            promotion_discount_amount_yuan(percent, 300) == 30
        ),
        "invalid_cross_type_fields_rejected": invalid_rejected,
        "model_schema_has_discriminator": "promotion_type" in strategy_schema,
        "model_schema_hides_selected_discount": (
            "selected_discount" not in strategy_schema
        ),
        "tool_schema_uses_canonical_yuan": (
            "discount_amount_yuan" in margin_schema["properties"]
            and "discount" not in margin_schema["properties"]
        ),
        "explicit_legacy_unit_migrates": explicit.status == "migrated",
        "ambiguous_legacy_unit_requires_regeneration": (
            ambiguous.status == "requires_regeneration"
        ),
        "strategy_result_is_typed": (
            strategy["promotion_protocol_version"] == "1.0"
            and strategy["promotion"]["currency"] == "CNY"
        ),
        "artifact_declares_protocol": artifact.promotion_protocol_version == "1.0",
        "compatibility_coupon_matches_tool": (
            strategy["coupon"] == strategy["margin"]["discount_amount_yuan"]
        ),
    }
    report = {
        "version": "v56",
        "project_version": PROJECT_VERSION,
        "passed": all(checks.values()),
        "checks": checks,
        "task_id": state.task_id,
        "run_id": state.run_id,
        "promotion": strategy["promotion"],
        "boundary": (
            "v56 types promotion units and migrates legacy state. Dynamic model-generated "
            "candidate proposals and selection remain v57 scope."
        ),
    }
    target = PROJECT_ROOT / "reports" / "v56" / "v56_acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
