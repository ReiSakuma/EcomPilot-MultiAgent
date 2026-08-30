from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


WRITE_OPERATIONS = {"update_listing", "create_coupon", "update_price", "publish_listing"}

TOOL_PERMISSION_MATRIX: dict[str, set[str]] = {
    "market_agent": {
        "search_products",
        "search_keywords",
        "get_reviews",
        "analyze_review_pain_points",
        "analyze_feature_frequency",
        "build_market_report",
        "query_market_database",
    },
    "strategy_agent": {
        "calculate_margin",
        "suggest_discount",
        "suggest_discount_amount_yuan",
        "check_inventory",
        "forecast_demand",
        "query_campaign_history",
        "analyze_competitor_price_trends",
    },
    "browser_agent": {"browser_execute", "browser_verify", "get_seller_center_snapshot"},
    "analytics_agent": {
        "get_sales_metrics",
        "compare_sales_periods",
        "get_campaign_performance",
        "get_inventory_history",
    },
    "supervisor": {"get_seller_center_snapshot"},
}


class ToolPermissionError(PermissionError):
    pass


class ToolApprovalRequiredError(PermissionError):
    pass


def classify_operation(operation: str) -> RiskLevel:
    if operation in WRITE_OPERATIONS:
        return RiskLevel.high
    if operation.endswith("_draft"):
        return RiskLevel.medium
    return RiskLevel.low


def assert_tool_permission(
    agent_name: str, tool_name: str, allowed_agents: set[str] | None = None
) -> None:
    if allowed_agents is not None:
        permitted = agent_name in allowed_agents
    else:
        permitted = tool_name in TOOL_PERMISSION_MATRIX.get(agent_name, set())
    if not permitted:
        raise ToolPermissionError(f"Agent '{agent_name}' is not allowed to call '{tool_name}'")
