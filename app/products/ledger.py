from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.config import CONVERSATION_DATABASE_PATH
from app.conversations.repository import ConversationRepository
from app.orchestration.state import TaskState
from app.orchestration.failures import failure_from_exception
from app.products.models import (
    ProductDetail,
    ProductEvent,
    ProductRecord,
    TaskProductLink,
)


class ProductLedgerError(RuntimeError):
    pass


class ProductNotFoundError(ProductLedgerError):
    pass


class ProductLedger:
    """Tenant-scoped product identity and task/artifact relationship store."""

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

    def record_successful_execution(self, state: TaskState) -> ProductRecord:
        browser = state.agent_outputs.get("browser_agent", {})
        verification = browser.get("verification", {})
        if not verification.get("verified"):
            raise ProductLedgerError("Only verified browser execution may enter Product Ledger")
        review = state.require_agent_output("review_agent", required_keys=("execution_plan",))
        plan = dict(review["execution_plan"])
        product_id = str(plan["product_id"])
        observed = dict(verification.get("observed", {}).get("product") or {})
        title = str(observed.get("title") or plan.get("title") or product_id)
        category = str(state.constraints.get("category") or "未分类")
        status = str(observed.get("status") or "draft")
        if status not in {"draft", "published"}:
            status = "draft"
        sku = f"SKU-{state.task_id.removeprefix('task_').upper()}"
        relation = "modified" if state.intent == "modify_listing" else "created"
        artifact_refs = sorted(set(state.latest_artifacts.values()))
        execution_result = dict(browser.get("browser_result") or {})
        resource_version = int(execution_result.get("resource_version") or 0)
        now = _now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if relation == "modified":
                existing = connection.execute(
                    "SELECT sku FROM product_ledger WHERE tenant_id=? AND product_id=?",
                    (state.principal.tenant_id, product_id),
                ).fetchone()
                if existing is None:
                    raise ProductLedgerError("Modified product is absent from Product Ledger")
                sku = str(existing["sku"] or sku)
            connection.execute(
                """INSERT INTO product_ledger(
                    tenant_id, product_id, sku, title, category, status,
                    source_task_id, seller_snapshot, resource_version, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, product_id) DO UPDATE SET
                    sku=excluded.sku,
                    title=excluded.title,
                    category=excluded.category,
                    status=excluded.status,
                    seller_snapshot=excluded.seller_snapshot,
                    resource_version=MAX(product_ledger.resource_version, excluded.resource_version),
                    updated_at=excluded.updated_at""",
                (
                    state.principal.tenant_id,
                    product_id,
                    sku,
                    title,
                    category,
                    status,
                    state.task_id,
                    json.dumps(observed, ensure_ascii=False),
                    resource_version,
                    now,
                    now,
                ),
            )
            aliases = {
                product_id: "product_id",
                sku: "sku",
                _normalize(title): "title",
                _normalize(category): "category",
            }
            for alias, alias_type in aliases.items():
                if not alias:
                    continue
                connection.execute(
                    """INSERT OR IGNORE INTO product_aliases(
                        tenant_id, product_id, alias, alias_type, created_at
                    ) VALUES(?, ?, ?, ?, ?)""",
                    (state.principal.tenant_id, product_id, alias, alias_type, now),
                )
            connection.execute(
                """INSERT OR IGNORE INTO task_product_links(
                    tenant_id, task_id, product_id, conversation_id, relation,
                    artifact_refs, linked_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (
                    state.principal.tenant_id,
                    state.task_id,
                    product_id,
                    state.conversation_id,
                    relation,
                    json.dumps(artifact_refs, ensure_ascii=False),
                    now,
                ),
            )
            if state.conversation_id:
                connection.execute(
                    """UPDATE conversations SET active_product_id=?, updated_at=?
                    WHERE tenant_id=? AND conversation_id=?""",
                    (
                        product_id,
                        now,
                        state.principal.tenant_id,
                        state.conversation_id,
                    ),
                )
            self._record_execution_events(
                connection,
                state,
                product_id=product_id,
                plan=plan,
                observed=observed,
                now=now,
            )
            connection.commit()
        product = self.get(state.principal.tenant_id, product_id)
        from app.analytics.store import AnalyticsStore

        try:
            AnalyticsStore(self.database_path).ensure_synthetic_history(
                state.principal.tenant_id,
                product_id,
                price=float(observed.get("price") or plan.get("price") or 0),
                initial_inventory=int(observed.get("stock") or plan.get("stock") or 0),
            )
        except Exception as exc:
            # Analytics is a later read-only capability; its seed failure must not
            # roll back an already verified seller-center execution.
            state.degradations.append(
                failure_from_exception(
                    exc,
                    stage="analytics_seed",
                    agent_name="product_ledger",
                    trace_refs=(state.run_id,),
                )
            )
        return product

    def _record_execution_events(
        self,
        connection: sqlite3.Connection,
        state: TaskState,
        *,
        product_id: str,
        plan: dict[str, Any],
        observed: dict[str, Any],
        now: str,
    ) -> None:
        initial_event = (
            "listing_revised",
            "已有商品已按字段级变更计划更新",
            {"change_plan": state.constraints.get("change_plan", [])},
        ) if state.intent == "modify_listing" else (
            "listing_created",
            "商品页面方案已生成",
            {"artifact_ref": state.latest_artifacts.get("listing_agent")},
        )
        events: list[tuple[str, str, dict[str, Any]]] = [
            initial_event,
            (
                "reviewed",
                "商品方案已通过执行前审核",
                {"artifact_ref": state.latest_artifacts.get("review_agent")},
            ),
            (
                "store_synced",
                "商品信息已写入并回读模拟店铺",
                {"operation": plan.get("operation"), "seller_status": observed.get("status")},
            ),
        ]
        if float(plan.get("coupon") or 0) > 0:
            events.append(
                (
                    "promotion_activated",
                    "优惠活动已同步到模拟店铺",
                    {"coupon": plan.get("coupon")},
                )
            )
        if observed.get("status") == "published":
            events.append(("published", "商品已发布", {}))
        corrections = list(
            state.agent_outputs.get("listing_agent", {}).get("semantic_corrections") or []
        )
        if corrections:
            events.insert(
                1,
                (
                    "listing_revised",
                    "商品表述在审核前完成受控修订",
                    {"correction_count": len(corrections)},
                ),
            )
        for position, (event_type, summary, details) in enumerate(events):
            key = f"{state.task_id}:{product_id}:{event_type}"
            connection.execute(
                """INSERT OR IGNORE INTO product_events(
                    event_id, tenant_id, product_id, task_id, conversation_id,
                    event_type, status, summary, details, idempotency_key, occurred_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)""",
                (
                    f"event_{uuid4().hex[:12]}",
                    state.principal.tenant_id,
                    product_id,
                    state.task_id,
                    state.conversation_id,
                    event_type,
                    summary,
                    json.dumps(details, ensure_ascii=False),
                    key,
                    _offset_timestamp(now, position),
                ),
            )

    def get(
        self, tenant_id: str, product_id: str, *, include_deleted: bool = False
    ) -> ProductRecord:
        clause = "" if include_deleted else " AND status != 'deleted'"
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM product_ledger WHERE tenant_id=? AND product_id=?{clause}",
                (tenant_id, product_id),
            ).fetchone()
        if row is None:
            raise ProductNotFoundError("Product not found")
        return _product(row)

    def list_products(self, tenant_id: str, *, limit: int = 100) -> list[ProductRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM product_ledger
                WHERE tenant_id=? AND status!='deleted'
                ORDER BY updated_at DESC LIMIT ?""",
                (tenant_id, max(1, min(limit, 200))),
            ).fetchall()
        return [_product(row) for row in rows]

    def find_by_alias(self, tenant_id: str, alias: str) -> list[ProductRecord]:
        normalized = _normalize(alias)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT p.* FROM product_ledger p
                JOIN product_aliases a
                  ON a.tenant_id=p.tenant_id AND a.product_id=p.product_id
                WHERE p.tenant_id=? AND p.status!='deleted'
                  AND (a.alias=? OR a.alias LIKE ? OR ? LIKE '%' || a.alias || '%')
                ORDER BY p.updated_at DESC""",
                (tenant_id, normalized, f"%{normalized}%", normalized),
            ).fetchall()
        return [_product(row) for row in rows]

    def find_by_exact_alias(self, tenant_id: str, alias: str) -> list[ProductRecord]:
        """Resolve an exact identity label before considering fuzzy containment."""

        normalized = _normalize(alias)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT p.* FROM product_ledger p
                JOIN product_aliases a
                  ON a.tenant_id=p.tenant_id AND a.product_id=p.product_id
                WHERE p.tenant_id=? AND p.status!='deleted' AND a.alias=?
                ORDER BY p.updated_at DESC""",
                (tenant_id, normalized),
            ).fetchall()
        return [_product(row) for row in rows]

    def products_for_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> list[ProductRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT p.* FROM product_ledger p
                JOIN task_product_links l
                  ON l.tenant_id=p.tenant_id AND l.product_id=p.product_id
                WHERE p.tenant_id=? AND l.conversation_id=? AND p.status!='deleted'
                ORDER BY l.linked_at DESC""",
                (tenant_id, conversation_id),
            ).fetchall()
        return [_product(row) for row in rows]

    def product_for_task(self, tenant_id: str, task_id: str) -> ProductRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT p.* FROM product_ledger p
                JOIN task_product_links l
                  ON l.tenant_id=p.tenant_id AND l.product_id=p.product_id
                WHERE p.tenant_id=? AND l.task_id=? AND p.status!='deleted'
                ORDER BY l.linked_at DESC LIMIT 1""",
                (tenant_id, task_id),
            ).fetchone()
        return _product(row) if row else None

    def detail(self, tenant_id: str, product_id: str) -> ProductDetail:
        product = self.get(tenant_id, product_id)
        with self._connect() as connection:
            links = connection.execute(
                """SELECT * FROM task_product_links
                WHERE tenant_id=? AND product_id=? ORDER BY linked_at""",
                (tenant_id, product_id),
            ).fetchall()
            events = connection.execute(
                """SELECT * FROM product_events
                WHERE tenant_id=? AND product_id=? ORDER BY occurred_at, event_id""",
                (tenant_id, product_id),
            ).fetchall()
        return ProductDetail(
            product=product,
            task_links=[_link(row) for row in links],
            timeline=[_event(row) for row in events],
            seller_state_available=bool(product.seller_snapshot),
        )

    def mark_deleted(self, tenant_id: str, product_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE product_ledger SET status='deleted', updated_at=?
                WHERE tenant_id=? AND product_id=?""",
                (_now(), tenant_id, product_id),
            )


def stable_product_id(task_id: str) -> str:
    return f"product_{task_id.removeprefix('task_').lower()}"


def _product(row: sqlite3.Row) -> ProductRecord:
    return ProductRecord(
        tenant_id=row["tenant_id"],
        product_id=row["product_id"],
        sku=row["sku"],
        title=row["title"],
        category=row["category"],
        status=row["status"],
        source_task_id=row["source_task_id"],
        seller_snapshot=json.loads(row["seller_snapshot"] or "{}"),
        resource_version=int(row["resource_version"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _link(row: sqlite3.Row) -> TaskProductLink:
    return TaskProductLink(
        tenant_id=row["tenant_id"],
        task_id=row["task_id"],
        product_id=row["product_id"],
        conversation_id=row["conversation_id"],
        relation=row["relation"],
        artifact_refs=json.loads(row["artifact_refs"] or "[]"),
        linked_at=row["linked_at"],
    )


def _event(row: sqlite3.Row) -> ProductEvent:
    return ProductEvent(
        event_id=row["event_id"],
        tenant_id=row["tenant_id"],
        product_id=row["product_id"],
        task_id=row["task_id"],
        conversation_id=row["conversation_id"],
        event_type=row["event_type"],
        status=row["status"],
        summary=row["summary"],
        details=json.loads(row["details"] or "{}"),
        idempotency_key=row["idempotency_key"],
        occurred_at=row["occurred_at"],
    )


def _normalize(value: str) -> str:
    return "".join(str(value).lower().split())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _offset_timestamp(timestamp: str, microseconds: int) -> str:
    value = datetime.fromisoformat(timestamp)
    return value.replace(microsecond=min(999999, value.microsecond + microseconds)).isoformat()
