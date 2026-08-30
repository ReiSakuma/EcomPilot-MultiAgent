from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PROJECT_VERSION
from app.safety.content_revision import normalize_listing_semantics
from app.safety.strategy_rendering import (
    STRATEGY_RENDER_VERSION,
    render_authoritative_strategy,
    verify_authoritative_strategy,
)
from app.seller_center.schemas import ExecutionPlan


def main() -> None:
    strategy = {
        "price": 300,
        "promotion": {"promotion_type": "fixed_amount_coupon", "discount_amount_yuan": 10},
        "coupon": 10,
        "planned_units": 300,
        "margin": {},
        "inventory_check": {},
        "launch_plan": "到手价1元，毛利率99%。",
        "strategy_rationale": "到手价1元，毛利率99%。",
        "candidate_proposals": [{
            "candidate_id": "candidate_safe",
            "objective": "游戏场景首发券",
            "promotion": {"promotion_type": "fixed_amount_coupon", "discount_amount_yuan": 10},
            "planned_units": 300,
        }],
        "candidate_evaluations": [{
            "candidate_id": "candidate_safe",
            "eligible": True,
            "promotion": {"promotion_type": "fixed_amount_coupon", "discount_amount_yuan": 10},
            "discount_amount_yuan": 10,
            "planned_units": 300,
        }],
        "selected_candidate_id": "candidate_safe",
    }
    constraints = {
        "category": "无线耳机",
        "target_audience": "游戏爱好者",
        "target_price": 300,
        "cost": 95,
        "inventory": 800,
    }
    corrections = render_authoritative_strategy(strategy, constraints, category="无线耳机")
    listing = {
        "title": "第一主动降噪无线耳机 蓝牙5.3",
        "keywords": ["主动降噪", "无线耳机"],
        "bullets": ["主动降噪", "支持蓝牙5.3"],
    }
    listing_corrections = normalize_listing_semantics(
        listing,
        category="无线耳机",
        confirmed_features=["蓝牙5.3"],
        confirmed_product_form=None,
    )
    plan = ExecutionPlan(
        operation="update_listing",
        product_id="v58_acceptance",
        title=listing["title"],
        bullets=listing["bullets"],
        price=strategy["price"],
        stock=800,
        coupon=strategy["coupon"],
        source_artifact_hashes={"listing_agent": "a" * 64, "strategy_agent": "b" * 64},
    )
    checks = {
        "project_version": PROJECT_VERSION == "0.58.0",
        "deterministic_render_enabled": strategy["strategy_render_version"] == STRATEGY_RENDER_VERSION,
        "wrong_model_numbers_removed": all(token not in strategy["launch_plan"] for token in ("1元", "99%")),
        "tool_numbers_rendered": all(
            token in strategy["launch_plan"]
            for token in ("标价300元", "10元优惠券", "到手价290元", "毛利率67.24%")
        ),
        "ownership_assertion_passes": not verify_authoritative_strategy(strategy, constraints),
        "strategy_audit_hashed": bool(corrections)
        and all(item.get("before_hash") and item.get("after_hash") for item in corrections),
        "listing_local_repair": "主动降噪" not in json.dumps(
            {key: listing[key] for key in ("title", "keywords", "bullets")}, ensure_ascii=False
        ),
        "listing_audit_hashed": bool(listing_corrections)
        and all(item.get("before_hash") and item.get("after_hash") for item in listing_corrections),
        "execution_payload_bound": bool(plan.payload_hash) and len(plan.source_artifact_hashes) == 2,
    }
    report = {
        "version": "v58",
        "project_version": PROJECT_VERSION,
        "passed": all(checks.values()),
        "checks": checks,
        "strategy": strategy,
        "listing": listing,
        "execution_plan": plan.model_dump(mode="json"),
    }
    output = Path("reports/v58/v58_acceptance.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
