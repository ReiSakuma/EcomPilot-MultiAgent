from __future__ import annotations

import re

from app.access.models import AccessPrincipal, default_principal
from app.orchestration.state import TaskNode, TaskState


_FEATURE_ALIASES = {
    "游戏低延迟": ("游戏低延迟", "游戏低延时", "低延迟", "低延时"),
    "快充": ("快充", "快冲"),
}
_PRODUCT_FORM_ALIASES = {
    "头戴式": "头戴式",
    "入耳式": "入耳式",
    "入耳试": "入耳式",
    "半入耳式": "半入耳式",
    "半入耳试": "半入耳式",
    "开放式": "开放式",
    "耳夹式": "耳夹式",
}


def normalize_product_form(value: object) -> str | None:
    normalized = str(value or "").strip()
    return _PRODUCT_FORM_ALIASES.get(normalized)


def normalize_confirmed_features(values: list[object]) -> list[str]:
    """Canonicalize grounded feature spelling without inventing new facts."""

    normalized: list[str] = []
    for value in values:
        for raw in re.split(r"[、,，;；+]|(?:以及|和|及)", str(value)):
            feature = raw.strip()
            if not feature or feature in {"暂无", "暂无补充", "未确认", "无"}:
                continue
            if normalize_product_form(feature):
                continue
            canonical = next(
                (
                    name
                    for name, aliases in _FEATURE_ALIASES.items()
                    if feature in aliases
                ),
                feature,
            )
            if canonical not in normalized:
                normalized.append(canonical)
    return normalized


def extract_constraints(goal: str) -> dict[str, object]:
    constraints: dict[str, object] = {}
    cost_match = re.search(
        r"(?:成本|进货(?:价)?|拿货(?:价)?|采购(?:价)?)"
        r"\s*(?:为|是)?\s*(-?\d+(?:\.\d+)?)\s*(?:元|块)?",
        goal,
    )
    price_match = re.search(
        r"(?:目标售价|计划售价|售价|定价|想卖|准备卖|打算卖)"
        r"\s*(?:为|是)?\s*(-?\d+(?:\.\d+)?)\s*(?:元|块)?",
        goal,
    )
    inventory_match = re.search(
        r"(?:库存|库村|存货)\s*(?:为|是)?\s*(-?\d+)\s*(?:件|个)?",
        goal,
    )
    planned_units_match = re.search(
        r"(?:计划销量|计划首月销量|首月卖|计划投入|首批投入|投入|只能投入)"
        r"\s*(\d+)\s*(?:件|个)?",
        goal,
    )
    margin_match = re.search(
        r"(?:最低毛利率|毛利率|毛利)"
        r"\s*(?:要求|不能低于|不低于|至少|最少|为|是)?\s*"
        r"(-?\d+(?:\.\d+)?)\s*%",
        goal,
    )
    margin_cheng_match = re.search(
        r"(?:最低毛利率|毛利率|毛利)"
        r"\s*(?:要求|不能低于|不低于|至少|最少|为|是)?\s*"
        r"([一二三四五六七八九])成",
        goal,
    )
    audience_match = re.search(r"(?:面向|主要卖给)([^，,。]+)", goal)
    confirmed_features_match = re.search(
        r"(?:已确认(?:的)?(?:产品)?功能(?:只有|仅有|包括|包含|是|为)?|"
        r"确认(?:产品)?功能(?:只有|仅有|包括|包含|是|为)?|"
        r"(?:产品)?功能有)"
        r"\s*[：:]?\s*(.*?)"
        r"(?=。|；|;|但是|但|然而|主要卖给|(?:主要)?面向|运营目标\s*[：:]|已确认(?:的)?产品形态|$)",
        goal,
    )
    confirmed_form_match = re.search(
        r"已确认的产品形态\s*[：:]\s*([^，,。]+)", goal
    )
    explicit_form_match = re.search(
        r"(头戴式|入耳式|入耳试|半入耳式|半入耳试|开放式|耳夹式)"
        r"(?=(?:无线|蓝牙|游戏)?耳机)",
        goal,
    )

    if cost_match:
        constraints["cost"] = float(cost_match.group(1))
    if price_match:
        constraints["target_price"] = float(price_match.group(1))
    if inventory_match:
        constraints["inventory"] = int(inventory_match.group(1))
    if planned_units_match:
        constraints["planned_units"] = int(planned_units_match.group(1))
    if margin_match:
        constraints["min_margin_rate"] = float(margin_match.group(1)) / 100
    elif margin_cheng_match:
        chinese_digits = "一二三四五六七八九"
        constraints["min_margin_rate"] = (
            chinese_digits.index(margin_cheng_match.group(1)) + 1
        ) / 10
    if audience_match:
        constraints["target_audience"] = audience_match.group(1).strip()
    if confirmed_features_match:
        raw_features = [
            item.strip()
            for item in re.split(
                r"[、,，;；]", confirmed_features_match.group(1)
            )
            if item.strip() and item.strip() not in {"暂无", "暂无补充", "未确认", "无"}
        ]
        constraints["confirmed_features"] = normalize_confirmed_features(raw_features)
        for item in raw_features:
            for candidate in re.split(r"[、,，;；+]|(?:以及|和|及)", item):
                if product_form := normalize_product_form(candidate):
                    constraints.setdefault("confirmed_product_form", product_form)
    if confirmed_form_match:
        confirmed_form = confirmed_form_match.group(1).strip()
        if confirmed_form not in {"未确认", "暂无", "暂无补充"}:
            constraints["confirmed_product_form"] = (
                normalize_product_form(confirmed_form) or confirmed_form
            )
    elif explicit_form_match:
        # A concrete noun phrase such as "入耳式无线耳机" is an explicit product
        # fact even without a form-style label.
        constraints["confirmed_product_form"] = (
            normalize_product_form(explicit_form_match.group(1))
            or explicit_form_match.group(1)
        )
    category_aliases = {
        "无线而机": "无线耳机",
        "蓝牙而机": "蓝牙耳机",
        "游戏而机": "游戏耳机",
        "机械建盘": "机械键盘",
    }
    for alias, category in category_aliases.items():
        if alias in goal:
            constraints["category"] = category
            break
    known_categories = (
        "无线耳机", "蓝牙耳机", "头戴式耳机", "游戏耳机", "智能水杯",
        "机械键盘", "智能手表", "移动电源", "咖啡机", "护肤品",
    )
    for category in known_categories:
        if "category" not in constraints and category in goal:
            constraints["category"] = category
            break
    if "注入违规标题" in goal:
        constraints["force_bad_title"] = True
    if "模拟执行验证失败" in goal:
        constraints["force_execution_verification_failure"] = True
    return constraints


class Planner:
    def build_initial_state(
        self,
        goal: str,
        approved: bool = False,
        approved_by: str | None = None,
        approval_reason: str | None = None,
        principal: AccessPrincipal | None = None,
    ) -> TaskState:
        state = TaskState(
            goal=goal,
            principal=principal or default_principal(),
            approved=approved,
            approved_by=approved_by if approved else None,
            approval_reason=approval_reason if approved else None,
        )
        state.constraints = extract_constraints(goal)
        state.todo = ["竞品调研", "市场价格检查", "Listing生成", "促销策略", "约束审核", "模拟执行"]
        state.nodes = {
            "market": TaskNode(
                node_id="market",
                agent_name="market_agent",
                capability_id="market.research",
            ),
            "market_price_gate": TaskNode(
                node_id="market_price_gate",
                agent_name="market_price_gate_agent",
                dependencies=["market"],
                capability_id="market.price_assess",
            ),
            "listing": TaskNode(
                node_id="listing",
                agent_name="listing_agent",
                dependencies=["market", "market_price_gate"],
                capability_id="listing.compose",
            ),
            "strategy": TaskNode(
                node_id="strategy",
                agent_name="strategy_agent",
                dependencies=["market", "market_price_gate"],
                capability_id="strategy.plan",
            ),
            "review": TaskNode(
                node_id="review",
                agent_name="review_agent",
                dependencies=["listing", "strategy"],
                capability_id="risk.review",
            ),
            "browser": TaskNode(
                node_id="browser",
                agent_name="browser_agent",
                dependencies=["review"],
                capability_id="seller.execute",
            ),
        }
        return state

    def build_market_research_state(
        self,
        goal: str,
        *,
        principal: AccessPrincipal | None = None,
        constraints: dict[str, object] | None = None,
    ) -> TaskState:
        """Build the smallest read-only DAG for a market information request."""

        state = TaskState(
            goal=goal,
            principal=principal or default_principal(),
            approved=False,
            intent="market_research",
        )
        state.constraints = constraints or extract_constraints(goal)
        state.todo = ["市场调研"]
        state.nodes = {
            "market": TaskNode(
                node_id="market",
                agent_name="market_agent",
                capability_id="market.research",
            )
        }
        return state

    def build_product_performance_state(
        self,
        goal: str,
        *,
        principal: AccessPrincipal | None = None,
        constraints: dict[str, object],
    ) -> TaskState:
        """Build an isolated read-only subgraph for one resolved product."""

        state = TaskState(
            goal=goal,
            principal=principal or default_principal(),
            approved=False,
            intent="product_performance",
        )
        state.constraints = dict(constraints)
        state.entity_refs = [str(constraints["product_id"])]
        state.todo = ["销售表现分析"]
        state.nodes = {
            "analytics": TaskNode(
                node_id="analytics",
                agent_name="analytics_agent",
                capability_id="analytics.read",
            )
        }
        return state
