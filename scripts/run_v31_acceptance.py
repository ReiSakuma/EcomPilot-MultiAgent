from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.tools.analytics_tools as analytics_tools
from app.access.context import tenant_scope
from app.analytics.store import AnalyticsDataUnavailableError, AnalyticsStore
from app.conversations.repository import ConversationRepository


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ecompilot_v31_") as directory:
        database = Path(directory) / "acceptance.db"
        ConversationRepository(database)
        now = "2026-08-28T00:00:00+00:00"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """INSERT INTO product_ledger(
                    tenant_id, product_id, sku, title, category, status,
                    source_task_id, seller_snapshot, created_at, updated_at
                ) VALUES('tenant_demo', 'product_acceptance', 'SKU-ACCEPTANCE',
                    '验收无线耳机', '无线耳机', 'published', 'task_acceptance',
                    '{}', ?, ?)""",
                (now, now),
            )
        store = AnalyticsStore(database)
        store.ensure_synthetic_history(
            "tenant_demo", "product_acceptance", price=300,
            initial_inventory=800, end_date=date(2026, 8, 28),
        )
        original_store = analytics_tools.AnalyticsStore
        analytics_tools.AnalyticsStore = lambda: AnalyticsStore(database)
        checks: list[dict] = []
        try:
            with tenant_scope("tenant_demo"):
                for days in (7, 14, 30, 45, 60):
                    end = date(2026, 8, 28)
                    start = end - timedelta(days=days - 1)
                    result = analytics_tools.get_sales_metrics(
                        "product_acceptance", start.isoformat(), end.isoformat()
                    )
                    with sqlite3.connect(database) as connection:
                        expected = connection.execute(
                            """SELECT SUM(units_sold), SUM(revenue), SUM(orders),
                                MAX(source_updated_at) FROM daily_product_metrics
                            WHERE tenant_id=? AND product_id=?
                            AND metric_date BETWEEN ? AND ?""",
                            ("tenant_demo", "product_acceptance", start.isoformat(), end.isoformat()),
                        ).fetchone()
                    checks.append(
                        {
                            "period_days": days,
                            "numeric_match": (
                                result["metrics"]["units_sold"] == expected[0]
                                and result["metrics"]["revenue"] == expected[1]
                                and result["metrics"]["orders"] == expected[2]
                            ),
                            "source_labeled": (
                                result["source_type"] == "synthetic_demo"
                                and result["source_updated_at"] == expected[3]
                            ),
                        }
                    )
            tenant_leakage = False
            with tenant_scope("tenant_beta"):
                try:
                    analytics_tools.get_sales_metrics(
                        "product_acceptance", "2026-08-01", "2026-08-28"
                    )
                    tenant_leakage = True
                except AnalyticsDataUnavailableError:
                    pass
        finally:
            analytics_tools.AnalyticsStore = original_store

        report = {
            "version": "v31",
            "status": "passed"
            if all(item["numeric_match"] and item["source_labeled"] for item in checks)
            and not tenant_leakage else "failed",
            "period_cases": len(checks),
            "numeric_field_match_rate": sum(item["numeric_match"] for item in checks) / len(checks),
            "source_label_rate": sum(item["source_labeled"] for item in checks) / len(checks),
            "cross_tenant_leakage_count": int(tenant_leakage),
            "data_source": "synthetic_demo",
            "checks": checks,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
