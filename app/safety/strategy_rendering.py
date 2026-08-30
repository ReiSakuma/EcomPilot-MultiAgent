from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.tools.inventory_tools import check_inventory
from app.tools.pricing_tools import calculate_margin


STRATEGY_RENDER_VERSION = "strategy-render-v1"
NUMERIC_OWNERSHIP_VERSION = "numeric-ownership-v1"


class StrategyRenderError(ValueError):
    pass


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _business_reason(text: str) -> str:
    """Keep model creativity while removing every authoritative numeric claim."""

    kept: list[str] = []
    for part in re.split(r"[。；;\n]+", str(text or "")):
        cleaned = part.strip(" ，,")
        if not cleaned:
            continue
        if re.search(r"\d|%|％|元|件|天|折", cleaned):
            continue
        kept.append(cleaned)
    return "；".join(kept)[:180]


def render_authoritative_strategy(
    result: dict[str, Any],
    constraints: dict[str, Any],
    *,
    category: str,
) -> list[dict[str, Any]]:
    """Project tool-owned numbers into Strategy prose and a traceable ownership map."""

    before = {
        "price": result.get("price"),
        "coupon": result.get("coupon"),
        "planned_units": result.get("planned_units"),
        "margin": result.get("margin"),
        "inventory_check": result.get("inventory_check"),
        "launch_plan": result.get("launch_plan"),
        "strategy_rationale": result.get("strategy_rationale"),
    }
    price = float(constraints.get("target_price", result.get("price") or 0))
    cost = float(constraints.get("cost", (result.get("margin") or {}).get("cost") or 0))
    inventory = int(constraints.get("inventory", 0))
    coupon = float(result.get("coupon") or 0)
    planned_units = int(result.get("planned_units") or 0)
    promotion = dict(result.get("promotion") or {})
    numeric_source = "strategy.verified_tool_results"

    margin = calculate_margin(price, cost, discount_amount_yuan=coupon)
    inventory_check = check_inventory(inventory, planned_units)
    result.update(
        {
            "price": price,
            "coupon": coupon,
            "planned_units": planned_units,
            "margin": margin,
            "inventory_check": inventory_check,
        }
    )
    if promotion:
        result["promotion"] = promotion

    audience = str(constraints.get("target_audience") or "目标人群")
    reason = _business_reason(str(result.get("strategy_rationale") or ""))
    positioning = reason or f"围绕{audience}验证{category}首发定位"
    promotion_text = (
        f"使用{coupon:g}元优惠券" if coupon > 0 else "本次不设置优惠券"
    )
    margin_percent = round(float(margin["margin_rate"]) * 100, 2)
    result["launch_plan"] = (
        f"首期{positioning}。标价{price:g}元，{promotion_text}，"
        f"预计到手价{float(margin['net_price']):g}元；首批计划投入{planned_units}件，"
        f"工具核算毛利率{margin_percent:g}%。"
    )
    result["strategy_rationale"] = (
        reason or "单一方案已通过价格、毛利、库存和促销约束核验"
    )
    ownership = {
        "protocol_version": NUMERIC_OWNERSHIP_VERSION,
        "fields": {
            "strategy.price": "task.constraints.target_price",
            "strategy.promotion": numeric_source + ".promotion",
            "strategy.coupon": numeric_source + ".discount_amount_yuan",
            "strategy.margin": "tool.calculate_margin",
            "strategy.planned_units": numeric_source + ".planned_units",
            "strategy.inventory_check": "tool.check_inventory",
        },
        "model_owned_fields": ["strategy.strategy_rationale"],
    }
    render_input = {
        "category": category,
        "audience": audience,
        "rationale": reason,
        "price": price,
        "coupon": coupon,
        "planned_units": planned_units,
        "margin": margin,
        "inventory_check": inventory_check,
    }
    result["strategy_render_version"] = STRATEGY_RENDER_VERSION
    result["numeric_ownership"] = ownership
    result["render_manifest"] = {
        "input_hash": canonical_hash(render_input),
        "output_hash": canonical_hash(
            {
                "launch_plan": result["launch_plan"],
                "strategy_rationale": result["strategy_rationale"],
            }
        ),
        "core_protocol_version": result.get("core_protocol_version"),
    }

    after = {
        "price": result["price"],
        "coupon": result["coupon"],
        "planned_units": result["planned_units"],
        "margin": result["margin"],
        "inventory_check": result["inventory_check"],
        "launch_plan": result["launch_plan"],
        "strategy_rationale": result["strategy_rationale"],
    }
    if before == after:
        return []
    correction = {
        "correction_id": "correction_" + canonical_hash(
            {"before": before, "after": after, "version": STRATEGY_RENDER_VERSION}
        )[:12],
        "source_agent": "strategy_agent",
        "field_path": "strategy.authoritative_projection",
        "issue_code": "deterministic_strategy_render",
        "before": before,
        "after": after,
        "before_hash": canonical_hash(before),
        "after_hash": canonical_hash(after),
        "reason": "最终业务数字统一由受信输入和本地工具投影，模型文字不拥有价格、毛利或库存",
        "evidence_refs": list(ownership["fields"].values()),
        "method": STRATEGY_RENDER_VERSION,
        "status": "corrected",
    }
    existing = list(result.get("semantic_corrections") or [])
    if correction["correction_id"] not in {
        item.get("correction_id") for item in existing
    }:
        existing.append(correction)
    result["semantic_corrections"] = existing
    return [correction]


def verify_authoritative_strategy(
    strategy: dict[str, Any], constraints: dict[str, Any]
) -> list[str]:
    if strategy.get("strategy_render_version") != STRATEGY_RENDER_VERSION:
        return ["strategy_render_version_missing"]
    expected = dict(strategy)
    render_authoritative_strategy(
        expected,
        constraints,
        category=str(constraints.get("category") or "商品"),
    )
    keys = (
        "price",
        "promotion",
        "coupon",
        "planned_units",
        "margin",
        "inventory_check",
        "launch_plan",
    )
    return [key for key in keys if strategy.get(key) != expected.get(key)]
