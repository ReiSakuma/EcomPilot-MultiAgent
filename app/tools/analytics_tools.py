from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.access.context import current_tenant_id
from app.analytics.models import DailyProductMetric
from app.analytics.store import AnalyticsDataUnavailableError, AnalyticsStore


def get_sales_metrics(product_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    rows = AnalyticsStore().daily_metrics(current_tenant_id(), product_id, start, end)
    if not rows:
        raise AnalyticsDataUnavailableError("Sales metrics are unavailable for this product and period")
    summary = _summary(rows)
    return {
        "status": "completed",
        "product_id": product_id,
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "metrics": summary,
        "daily_series": [
            {
                "date": row.metric_date.isoformat(),
                "units_sold": row.units_sold,
                "revenue": row.revenue,
                "orders": row.orders,
                "ending_inventory": row.ending_inventory,
            }
            for row in rows
        ],
        **_source(rows),
        "evidence_refs": [f"analytics://daily/{product_id}/{start}/{end}"],
    }


def compare_sales_periods(product_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    store = AnalyticsStore()
    current = store.daily_metrics(current_tenant_id(), product_id, start, end)
    previous = store.daily_metrics(
        current_tenant_id(), product_id, previous_start, previous_end
    )
    if not current or not previous:
        raise AnalyticsDataUnavailableError("Comparable sales periods are unavailable")
    current_summary, previous_summary = _summary(current), _summary(previous)
    return {
        "status": "completed",
        "product_id": product_id,
        "current_period": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "metrics": current_summary,
        },
        "previous_period": {
            "start_date": previous_start.isoformat(),
            "end_date": previous_end.isoformat(),
            "metrics": previous_summary,
        },
        "change": {
            "units_sold_rate": _change(current_summary["units_sold"], previous_summary["units_sold"]),
            "revenue_rate": _change(current_summary["revenue"], previous_summary["revenue"]),
            "conversion_rate_delta": round(
                current_summary["conversion_rate"] - previous_summary["conversion_rate"], 6
            ),
        },
        **_source([*current, *previous]),
        "evidence_refs": [f"analytics://compare/{product_id}/{start}/{end}"],
    }


def get_campaign_performance(product_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    campaigns = AnalyticsStore().campaigns(current_tenant_id(), product_id, start, end)
    if not campaigns:
        raise AnalyticsDataUnavailableError("Campaign metrics are unavailable for this period")
    return {
        "status": "completed",
        "product_id": product_id,
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "campaigns": [item.model_dump(mode="json") for item in campaigns],
        "summary": {
            "campaign_count": len(campaigns),
            "spend": round(sum(item.spend for item in campaigns), 2),
            "units_sold": sum(item.units_sold for item in campaigns),
            "revenue": round(sum(item.revenue for item in campaigns), 2),
            "weighted_roi": round(
                (sum(item.revenue for item in campaigns) - sum(item.spend for item in campaigns))
                / sum(item.spend for item in campaigns),
                4,
            ),
        },
        "source_type": campaigns[0].source_type,
        "source_updated_at": max(item.source_updated_at for item in campaigns).isoformat(),
        "evidence_refs": [f"analytics://campaign/{product_id}/{start}/{end}"],
    }


def get_inventory_history(product_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    store = AnalyticsStore()
    movements = store.inventory_history(current_tenant_id(), product_id, start, end)
    daily = store.daily_metrics(current_tenant_id(), product_id, start, end)
    if not daily:
        raise AnalyticsDataUnavailableError("Inventory history is unavailable for this period")
    return {
        "status": "completed",
        "product_id": product_id,
        "period": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "starting_inventory": (
            daily[0].ending_inventory + daily[0].units_sold - daily[0].refunds
        ),
        "ending_inventory": daily[-1].ending_inventory,
        "net_change": daily[-1].ending_inventory - (
            daily[0].ending_inventory + daily[0].units_sold - daily[0].refunds
        ),
        "movements": [item.model_dump(mode="json") for item in movements],
        **_source(daily),
        "evidence_refs": [f"analytics://inventory/{product_id}/{start}/{end}"],
    }


def _summary(rows: list[DailyProductMetric]) -> dict[str, Any]:
    impressions = sum(row.impressions for row in rows)
    clicks = sum(row.clicks for row in rows)
    orders = sum(row.orders for row in rows)
    units = sum(row.units_sold for row in rows)
    revenue = round(sum(row.revenue for row in rows), 2)
    refunds = sum(row.refunds for row in rows)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "orders": orders,
        "units_sold": units,
        "revenue": revenue,
        "refunds": refunds,
        "click_through_rate": round(clicks / impressions, 6) if impressions else 0.0,
        "conversion_rate": round(orders / clicks, 6) if clicks else 0.0,
        "average_order_value": round(revenue / orders, 2) if orders else 0.0,
        "ending_inventory": rows[-1].ending_inventory,
    }


def _source(rows: list[DailyProductMetric]) -> dict[str, Any]:
    source_types = sorted({row.source_type for row in rows})
    return {
        "source_type": source_types[0] if len(source_types) == 1 else "mixed",
        "source_updated_at": max(row.source_updated_at for row in rows).isoformat(),
        "data_points": len(rows),
    }


def _change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous, 6)
