from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from app.access.context import current_tenant_id
from app.config import SQL_DATABASE_PATH, SQL_MAX_ROWS
from app.sql.database import SQL_DATASET_VERSION, MarketDatabase
from app.sql.policy import ALLOWED_FUNCTIONS, SqlPolicyGateway
from app.sandbox.runner import SqlSandboxRunner
from app.distributed.bulkhead import GLOBAL_BULKHEADS


class MarketSqlService:
    def __init__(
        self,
        database_path: Path | None = None,
        *,
        max_rows: int = SQL_MAX_ROWS,
        timeout_seconds: float = 0.2,
        sandbox_runner: SqlSandboxRunner | None = None,
    ) -> None:
        self.policy = SqlPolicyGateway(max_rows=max_rows)
        self.database = MarketDatabase(
            database_path or SQL_DATABASE_PATH,
            timeout_seconds=timeout_seconds,
            sandbox_runner=sandbox_runner,
        )
        self.audit_records: list[dict[str, Any]] = []
        self._lock = RLock()

    def query(
        self,
        sql: str,
        purpose: str = "market_research",
        *,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        with GLOBAL_BULKHEADS.acquire("sql", blocking=True):
            return self._query_isolated(sql, purpose, tenant_id=tenant_id)

    def _query_isolated(
        self,
        sql: str,
        purpose: str = "market_research",
        *,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        effective_tenant = tenant_id or current_tenant_id()
        query_id = f"sqlquery_{uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc)
        try:
            decision = self.policy.authorize(sql, tenant_id=effective_tenant)
            execution = self.database.execute(decision)
        except Exception as exc:
            decision = getattr(exc, "decision", None)
            self._append_audit(
                {
                    "query_id": query_id,
                    "status": "denied" if decision is not None else "failed",
                    "purpose": purpose,
                    "tenant_id": effective_tenant,
                    "decision": decision.model_dump(mode="json") if decision else None,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "sandbox": getattr(exc, "receipt", None),
                    "created_at": created_at.isoformat(),
                }
            )
            raise
        result = {
            "query_id": query_id,
            "status": "completed",
            "purpose": purpose,
            "tenant_id": effective_tenant,
            "normalized_sql": decision.normalized_sql,
            "columns": execution["columns"],
            "rows": execution["rows"],
            "row_count": execution["row_count"],
            "truncated": execution["truncated"],
            "elapsed_ms": execution["elapsed_ms"],
            "dataset_version": execution["dataset_version"],
            "sandbox": execution["sandbox"],
            "data_trust": "untrusted_market_data",
            "policy": {
                "decision_id": decision.decision_id,
                "status": decision.status,
                "tables": list(decision.tables),
                "columns": list(decision.columns),
                "functions": list(decision.functions),
                "enforced_limit": decision.enforced_limit,
                "limit_applied": decision.limit_applied,
                "tenant_id": decision.tenant_id,
                "row_filter_applied": decision.row_filter_applied,
                "read_only_connection": True,
                "process_isolated": execution["sandbox"]["isolation"][
                    "separate_process"
                ],
                "reason_codes": list(decision.reason_codes),
            },
        }
        self._append_audit(
            {
                "query_id": query_id,
                "status": "completed",
                "purpose": purpose,
                "tenant_id": effective_tenant,
                "decision": decision.model_dump(mode="json"),
                "row_count": execution["row_count"],
                "elapsed_ms": execution["elapsed_ms"],
                "sandbox": execution["sandbox"],
                "created_at": created_at.isoformat(),
            }
        )
        return result

    def schema_catalog(self) -> dict[str, Any]:
        return {
            "dialect": "sqlite",
            "dataset": SQL_DATASET_VERSION,
            "access": "select_only",
            "tenant_isolation": "sqlglot_ast_row_filter",
            "max_rows": self.policy.max_rows,
            "allowed_functions": sorted(ALLOWED_FUNCTIONS),
            "tables": self.policy.schema_catalog(),
            "execution": self.database.sandbox_status(),
        }

    def sandbox_status(self) -> dict[str, Any]:
        return self.database.sandbox_status()

    def audits(
        self, limit: int = 50, *, tenant_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            records = self.audit_records
            if tenant_id is not None:
                records = [
                    record for record in records if record.get("tenant_id") == tenant_id
                ]
            return list(reversed(records[-max(1, min(limit, 200)) :]))

    def _append_audit(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.audit_records.append(record)


_SERVICE: MarketSqlService | None = None
_SERVICE_LOCK = RLock()


def get_market_sql_service() -> MarketSqlService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = MarketSqlService()
        return _SERVICE
