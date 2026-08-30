from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.analytics.models import CampaignMetric, DailyProductMetric, InventoryMovement
from app.config import CONVERSATION_DATABASE_PATH
from app.conversations.repository import ConversationRepository


class AnalyticsDataUnavailableError(RuntimeError):
    pass


class AnalyticsStore:
    """Strongly typed, tenant-scoped analytics facts stored beside Product Ledger."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or CONVERSATION_DATABASE_PATH
        ConversationRepository(self.database_path).migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            yield connection
        finally:
            connection.close()

    def ensure_synthetic_history(
        self,
        tenant_id: str,
        product_id: str,
        *,
        price: float,
        initial_inventory: int,
        days: int = 120,
        end_date: date | None = None,
    ) -> None:
        end = end_date or date.today()
        start = end - timedelta(days=max(30, min(days, 365)) - 1)
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            product = connection.execute(
                "SELECT 1 FROM product_ledger WHERE tenant_id=? AND product_id=? AND status!='deleted'",
                (tenant_id, product_id),
            ).fetchone()
            if product is None:
                raise AnalyticsDataUnavailableError("Product is unavailable for analytics")
            exists = connection.execute(
                "SELECT 1 FROM daily_product_metrics WHERE tenant_id=? AND product_id=? LIMIT 1",
                (tenant_id, product_id),
            ).fetchone()
            if exists:
                return
            connection.execute("BEGIN IMMEDIATE")
            inventory = max(0, initial_inventory)
            current = start
            index = 0
            while current <= end:
                seed = _number(product_id, current.isoformat())
                impressions = 320 + seed % 1280
                clicks = max(1, int(impressions * (0.045 + (seed % 45) / 1000)))
                orders = max(0, int(clicks * (0.055 + (seed % 35) / 1000)))
                units = min(inventory, orders + (1 if seed % 7 == 0 and orders else 0))
                refunds = 1 if units >= 4 and seed % 13 == 0 else 0
                inventory = max(0, inventory - units + refunds)
                revenue = round(units * price, 2)
                connection.execute(
                    """INSERT INTO daily_product_metrics(
                        tenant_id, product_id, metric_date, impressions, clicks, orders,
                        units_sold, revenue, refunds, ending_inventory, source_type,
                        source_updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synthetic_demo', ?)""",
                    (
                        tenant_id, product_id, current.isoformat(), impressions, clicks,
                        orders, units, revenue, refunds, inventory, updated_at,
                    ),
                )
                if index == 0:
                    self._insert_movement(
                        connection, tenant_id, product_id, current, "initial",
                        initial_inventory, initial_inventory, updated_at,
                    )
                if units:
                    self._insert_movement(
                        connection, tenant_id, product_id, current, "sale",
                        -units, max(0, inventory - refunds), updated_at,
                    )
                if refunds:
                    self._insert_movement(
                        connection, tenant_id, product_id, current, "refund",
                        refunds, inventory, updated_at,
                    )
                current += timedelta(days=1)
                index += 1
            self._insert_campaigns(
                connection, tenant_id, product_id, price, end, updated_at
            )
            connection.commit()

    @staticmethod
    def _insert_movement(
        connection: sqlite3.Connection,
        tenant_id: str,
        product_id: str,
        movement_date: date,
        movement_type: str,
        delta: int,
        ending_inventory: int,
        updated_at: str,
    ) -> None:
        connection.execute(
            """INSERT INTO inventory_movements(
                movement_id, tenant_id, product_id, movement_date, movement_type,
                quantity_delta, ending_inventory, source_type, source_updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'synthetic_demo', ?)""",
            (
                f"move_{uuid4().hex[:12]}", tenant_id, product_id,
                movement_date.isoformat(), movement_type, delta, ending_inventory, updated_at,
            ),
        )

    @staticmethod
    def _insert_campaigns(
        connection: sqlite3.Connection,
        tenant_id: str,
        product_id: str,
        price: float,
        end: date,
        updated_at: str,
    ) -> None:
        for index, (name, end_offset, duration, discount) in enumerate(
            (("首月冷启动", 45, 7, 10.0), ("会员周促销", 12, 5, 15.0))
        ):
            campaign_end = end - timedelta(days=end_offset)
            campaign_start = campaign_end - timedelta(days=duration - 1)
            units = 18 + _number(product_id, name) % 42
            revenue = round(units * max(0, price - discount), 2)
            spend = round(120 + (_number(name, product_id) % 280), 2)
            roi = round((revenue - spend) / spend, 4) if spend else 0.0
            connection.execute(
                """INSERT INTO campaign_metrics(
                    tenant_id, campaign_id, product_id, campaign_name, start_date,
                    end_date, discount, spend, units_sold, revenue, roi,
                    source_type, source_updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synthetic_demo', ?)""",
                (
                    tenant_id, f"campaign_{product_id}_{index}", product_id, name,
                    campaign_start.isoformat(), campaign_end.isoformat(), discount,
                    spend, units, revenue, roi, updated_at,
                ),
            )

    def daily_metrics(
        self, tenant_id: str, product_id: str, start_date: date, end_date: date
    ) -> list[DailyProductMetric]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM daily_product_metrics
                WHERE tenant_id=? AND product_id=? AND metric_date BETWEEN ? AND ?
                ORDER BY metric_date""",
                (tenant_id, product_id, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [DailyProductMetric.model_validate(dict(row)) for row in rows]

    def campaigns(
        self, tenant_id: str, product_id: str, start_date: date, end_date: date
    ) -> list[CampaignMetric]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM campaign_metrics
                WHERE tenant_id=? AND product_id=? AND end_date>=? AND start_date<=?
                ORDER BY start_date""",
                (tenant_id, product_id, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [CampaignMetric.model_validate(dict(row)) for row in rows]

    def inventory_history(
        self, tenant_id: str, product_id: str, start_date: date, end_date: date
    ) -> list[InventoryMovement]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM inventory_movements
                WHERE tenant_id=? AND product_id=? AND movement_date BETWEEN ? AND ?
                ORDER BY movement_date, movement_type""",
                (tenant_id, product_id, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        return [InventoryMovement.model_validate(dict(row)) for row in rows]


def _number(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12], 16)
