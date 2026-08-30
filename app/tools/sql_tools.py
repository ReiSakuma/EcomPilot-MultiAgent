from __future__ import annotations

from typing import Any

from app.sql.service import get_market_sql_service


def query_market_database(sql: str, purpose: str = "market_research") -> dict[str, Any]:
    return get_market_sql_service().query(sql, purpose)
