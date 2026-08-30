from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.orchestration.workflow import run_workflow
from app.tools.product_tools import build_market_report


EXPECTED = {
    "无线耳机": {
        "raw": 100,
        "valid": 98,
        "excluded": 2,
        "core_band": [129.0, 239.0],
        "full_band": [49.0, 899.0],
    },
    "机械键盘": {
        "raw": 100,
        "valid": 98,
        "excluded": 2,
        "core_band": [159.0, 499.0],
        "full_band": [39.0, 1299.0],
    },
}


def _layer_summary(report: dict) -> dict:
    layers = report["market_layers"]
    return {
        "mode": layers["mode"],
        "distribution_status": layers["distribution_status"],
        "core_reference_price": layers["core_reference_price"],
        "reference_method": layers["reference_method"],
        "core": layers["core_comparable"],
        "adjacent": layers["adjacent_tier"],
        "full_valid": layers["full_valid_market"],
        "adjacent_tier_bands": layers["adjacent_tier_bands"],
        "warnings": layers["warnings"],
        "content_hash": layers["content_hash"],
    }


def main() -> int:
    checks: dict[str, bool] = {}
    categories: dict[str, dict] = {}
    for category, expected in EXPECTED.items():
        report = build_market_report(category)
        sample = report["sample_size"]
        layers = report["market_layers"]
        prefix = "earbuds" if category == "无线耳机" else "keyboards"
        core_ids = set(layers["core_comparable"]["product_ids"])
        adjacent_ids = set(layers["adjacent_tier"]["product_ids"])
        full_ids = set(layers["full_valid_market"]["product_ids"])

        checks[f"{prefix}_raw_count"] = sample["raw_competitors"] == expected["raw"]
        checks[f"{prefix}_valid_count"] = sample["valid_competitors"] == expected["valid"]
        checks[f"{prefix}_excluded_count"] = sample["excluded_competitors"] == expected["excluded"]
        checks[f"{prefix}_layers_partition_valid_market"] = (
            not (core_ids & adjacent_ids)
            and core_ids | adjacent_ids == full_ids
            and len(full_ids) == expected["valid"]
        )
        checks[f"{prefix}_core_band"] = report["price_band"] == expected["core_band"]
        checks[f"{prefix}_full_band"] = report["full_market_band"] == expected["full_band"]
        checks[f"{prefix}_reviews_follow_products"] = (
            set(layers["core_comparable"]["review_product_ids"]) <= core_ids
            and set(layers["adjacent_tier"]["review_product_ids"]) <= adjacent_ids
        )
        categories[category] = {
            "sample_size": sample,
            **_layer_summary(report),
        }

    base_goal = (
        "我要上架一款成本 95 元的无线耳机，目标售价 {price} 元，"
        "主要面向游戏爱好者，库存 800 件，毛利率不能低于 25%。"
        "已确认功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
    )
    lower = run_workflow(base_goal.format(price=199), approved=False)
    higher = run_workflow(base_goal.format(price=699), approved=False)
    lower_layers = lower.agent_outputs["market_agent"]["market_layers"]
    higher_layers = higher.agent_outputs["market_agent"]["market_layers"]
    checks["target_price_does_not_change_core_ids"] = (
        lower_layers["core_comparable"]["product_ids"]
        == higher_layers["core_comparable"]["product_ids"]
    )
    checks["target_price_does_not_change_layer_hash"] = (
        lower_layers["content_hash"] == higher_layers["content_hash"]
    )

    earbuds_decisions = {
        item["sample_id"]: item
        for item in build_market_report(
            "无线耳机",
            target_audience="游戏爱好者",
            confirmed_features=["蓝牙5.3", "游戏低延迟", "长续航", "快充", "通话降噪"],
        )["market_layers"]["decisions"]
    }
    checks["premium_sample_is_adjacent"] = (
        earbuds_decisions["earbud_097"]["assigned_layer"] == "adjacent_tier"
        and earbuds_decisions["earbud_097"]["adjacent_group"] == "premium_or_luxury"
    )
    checks["clearance_sample_is_adjacent"] = (
        earbuds_decisions["earbud_095"]["assigned_layer"] == "adjacent_tier"
        and earbuds_decisions["earbud_095"]["adjacent_group"] == "entry_or_clearance"
    )

    report = {
        "version": "v53",
        "project_version": PROJECT_VERSION,
        "passed": all(checks.values()),
        "checks": checks,
        "categories": categories,
        "target_price_independence": {
            "lower_target_price": 199,
            "higher_target_price": 699,
            "lower_run_id": lower.run_id,
            "higher_run_id": higher.run_id,
            "core_ids_equal": checks["target_price_does_not_change_core_ids"],
            "layer_hash_equal": checks["target_price_does_not_change_layer_hash"],
            "content_hash": lower_layers["content_hash"],
        },
        "boundary": (
            "v53 builds target-price-independent core, adjacent, and full-valid market "
            "evidence. Price deviation gating and user confirmation begin in v54."
        ),
    }
    report_path = PROJECT_ROOT / "reports" / "v53" / "v53_acceptance.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
