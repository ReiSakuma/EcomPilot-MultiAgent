from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any

from app.config import DATA_DIR


DATASET_PATH = DATA_DIR / "strategy" / "strategy_evidence.json"


def _load_dataset() -> dict[str, Any]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _audience_matches(expected: str, actual: str) -> bool:
    normalize = lambda value: value.strip().replace("大学生", "学生")
    return normalize(expected) in normalize(actual) or normalize(actual) in normalize(expected)


def forecast_demand(
    category: str,
    target_audience: str,
    target_price: float,
    horizon_days: int = 30,
) -> dict[str, Any]:
    dataset = _load_dataset()
    matches = [
        signal
        for signal in dataset["demand_signals"]
        if signal["category"] == category
        and _audience_matches(target_audience, signal["audience"])
    ]
    if not matches:
        return {
            "status": "insufficient_evidence",
            "forecast_units": None,
            "range": None,
            "method": "weighted_recent_demand_with_price_elasticity",
            "evidence_refs": [],
            "dataset_version": dataset["dataset_version"],
            "source_type": dataset["source_type"],
        }
    signal = matches[0]
    recent = [float(value) for value in signal["weekly_units"][-4:]]
    weighted_weekly = sum(value * weight for value, weight in zip(recent, (1, 2, 3, 4))) / 10
    price_ratio = target_price / float(signal["reference_price"])
    adjusted_weekly = (
        weighted_weekly
        * (price_ratio ** float(signal["price_elasticity"]))
        * float(signal["seasonality_factor"])
    )
    forecast_units = max(0, round(adjusted_weekly * horizon_days / 7))
    return {
        "status": "completed",
        "forecast_units": forecast_units,
        "range": {
            "low": round(forecast_units * 0.8),
            "base": forecast_units,
            "high": round(forecast_units * 1.2),
        },
        "horizon_days": horizon_days,
        "target_price": target_price,
        "method": "weighted_recent_demand_with_price_elasticity",
        "evidence_refs": [signal["signal_id"]],
        "dataset_version": dataset["dataset_version"],
        "source_type": dataset["source_type"],
    }


def query_campaign_history(
    category: str,
    target_audience: str,
    limit: int = 5,
) -> dict[str, Any]:
    dataset = _load_dataset()
    campaigns = [
        campaign
        for campaign in dataset["campaigns"]
        if campaign["category"] == category
        and _audience_matches(target_audience, campaign["audience"])
    ]
    campaigns = sorted(campaigns, key=lambda item: float(item["roi"]), reverse=True)[:limit]
    return {
        "status": "completed" if campaigns else "insufficient_evidence",
        "campaigns": campaigns,
        "summary": {
            "sample_size": len(campaigns),
            "median_discount": median([item["discount"] for item in campaigns]) if campaigns else None,
            "best_roi_campaign_id": campaigns[0]["campaign_id"] if campaigns else None,
        },
        "evidence_refs": [item["campaign_id"] for item in campaigns],
        "dataset_version": dataset["dataset_version"],
        "source_type": dataset["source_type"],
    }


def analyze_competitor_price_trends(
    category: str,
    lookback_days: int = 60,
) -> dict[str, Any]:
    dataset = _load_dataset()
    trends: list[dict[str, Any]] = []
    for product in dataset["competitor_price_history"]:
        if product["category"] != category:
            continue
        points = product["points"][-max(2, min(len(product["points"]), lookback_days // 14 + 1)) :]
        first_price = float(points[0]["price"])
        latest_price = float(points[-1]["price"])
        change_rate = round((latest_price - first_price) / first_price, 4)
        trends.append(
            {
                "product_id": product["product_id"],
                "name": product["name"],
                "first_price": first_price,
                "latest_price": latest_price,
                "change_rate": change_rate,
                "direction": "down" if change_rate < 0 else ("up" if change_rate > 0 else "flat"),
                "latest_sales_index": points[-1]["sales_index"],
                "points": points,
            }
        )
    latest_prices = [item["latest_price"] for item in trends]
    return {
        "status": "completed" if trends else "insufficient_evidence",
        "lookback_days": lookback_days,
        "trends": trends,
        "summary": {
            "sample_size": len(trends),
            "latest_median_price": median(latest_prices) if latest_prices else None,
            "price_cuts": sum(item["direction"] == "down" for item in trends),
        },
        "evidence_refs": [item["product_id"] for item in trends],
        "dataset_version": dataset["dataset_version"],
        "source_type": dataset["source_type"],
    }
