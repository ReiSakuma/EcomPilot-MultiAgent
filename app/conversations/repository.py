from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterator
from uuid import uuid4

from app.config import CONVERSATION_DATABASE_PATH
from app.conversations.models import (
    BatchJobRecord,
    BatchItemRecord,
    ConversationDetail,
    ConversationRecord,
    ConversationSummary,
    MessageRecord,
    PendingRequestRecord,
    TaskIndexRecord,
    TaskSessionRecord,
    TaskRouteDecision,
    TurnRecord,
    TurnReservation,
    TurnTaskLinkRecord,
    TurnTaskRouteRecord,
    WorkflowRunRecord,
)
from app.orchestration.state import TaskState


SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
CONVERSATION_SCHEMA_VERSION = 13
_TERMINAL_TASK_SESSION_STATUSES = {
    "completed",
    "business_rejected",
    "technical_failed",
    "needs_attention",
    "cancelled",
}
_TERMINAL_WORKFLOW_RUN_STATUSES = set(_TERMINAL_TASK_SESSION_STATUSES)


class ConversationStoreError(RuntimeError):
    pass


class ConversationNotFoundError(ConversationStoreError):
    pass


class ConversationConflictError(ConversationStoreError):
    pass


class ConversationRepository:
    """Tenant-scoped SQLite index; checkpoints remain the task fact store."""

    _migration_lock = RLock()

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or CONVERSATION_DATABASE_PATH
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=15.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._migration_lock, self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
                    active_product_id TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    summary_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_tenant_updated
                    ON conversations(tenant_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS turns (
                    turn_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    client_request_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('processing', 'completed', 'failed')),
                    task_id TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(tenant_id, client_request_id),
                    UNIQUE(conversation_id, ordinal),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_turns_tenant_conversation
                    ON turns(tenant_id, conversation_id, ordinal);
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    intent TEXT NOT NULL DEFAULT 'create_listing',
                    task_id TEXT,
                    product_refs TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    FOREIGN KEY(turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_tenant_conversation
                    ON messages(tenant_id, conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS task_index (
                    task_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    FOREIGN KEY(turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_task_index_tenant_conversation
                    ON task_index(tenant_id, conversation_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS pending_requests (
                    conversation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('waiting_for_input', 'resolved', 'advisory')),
                    compiled_payload TEXT NOT NULL,
                    clarification_round INTEGER NOT NULL DEFAULT 0,
                    last_question TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS task_pending_requests (
                    task_session_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    checkpoint_thread_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('waiting_for_input','suspended','resolved','advisory')),
                    compiled_payload TEXT NOT NULL,
                    clarification_round INTEGER NOT NULL DEFAULT 0,
                    last_question TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_task_pending_conversation
                    ON task_pending_requests(tenant_id, conversation_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS product_ledger (
                    tenant_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    sku TEXT,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('draft', 'published', 'deleted')),
                    source_task_id TEXT NOT NULL,
                    seller_snapshot TEXT NOT NULL DEFAULT '{}',
                    resource_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, product_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_product_ledger_tenant_sku
                    ON product_ledger(tenant_id, sku) WHERE sku IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_product_ledger_tenant_updated
                    ON product_ledger(tenant_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS product_aliases (
                    tenant_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    alias_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, product_id, alias),
                    FOREIGN KEY(tenant_id, product_id)
                        REFERENCES product_ledger(tenant_id, product_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_product_aliases_lookup
                    ON product_aliases(tenant_id, alias);
                CREATE TABLE IF NOT EXISTS task_product_links (
                    tenant_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    conversation_id TEXT,
                    relation TEXT NOT NULL CHECK(relation IN ('created', 'modified', 'referenced')),
                    artifact_refs TEXT NOT NULL DEFAULT '[]',
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, task_id, product_id, relation),
                    FOREIGN KEY(tenant_id, product_id)
                        REFERENCES product_ledger(tenant_id, product_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_task_product_by_product
                    ON task_product_links(tenant_id, product_id, linked_at DESC);
                CREATE TABLE IF NOT EXISTS product_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    task_id TEXT,
                    conversation_id TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    UNIQUE(tenant_id, idempotency_key),
                    FOREIGN KEY(tenant_id, product_id)
                        REFERENCES product_ledger(tenant_id, product_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_product_events_timeline
                    ON product_events(tenant_id, product_id, occurred_at, event_id);
                CREATE TABLE IF NOT EXISTS daily_product_metrics (
                    tenant_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    metric_date TEXT NOT NULL,
                    impressions INTEGER NOT NULL CHECK(impressions >= 0),
                    clicks INTEGER NOT NULL CHECK(clicks >= 0),
                    orders INTEGER NOT NULL CHECK(orders >= 0),
                    units_sold INTEGER NOT NULL CHECK(units_sold >= 0),
                    revenue REAL NOT NULL CHECK(revenue >= 0),
                    refunds INTEGER NOT NULL CHECK(refunds >= 0),
                    ending_inventory INTEGER NOT NULL CHECK(ending_inventory >= 0),
                    source_type TEXT NOT NULL CHECK(source_type IN ('synthetic_demo', 'imported_file', 'platform_api')),
                    source_updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, product_id, metric_date),
                    FOREIGN KEY(tenant_id, product_id)
                        REFERENCES product_ledger(tenant_id, product_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_daily_metrics_range
                    ON daily_product_metrics(tenant_id, product_id, metric_date);
                CREATE TABLE IF NOT EXISTS campaign_metrics (
                    tenant_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    campaign_name TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    discount REAL NOT NULL CHECK(discount >= 0),
                    spend REAL NOT NULL CHECK(spend >= 0),
                    units_sold INTEGER NOT NULL CHECK(units_sold >= 0),
                    revenue REAL NOT NULL CHECK(revenue >= 0),
                    roi REAL NOT NULL,
                    source_type TEXT NOT NULL CHECK(source_type IN ('synthetic_demo', 'imported_file', 'platform_api')),
                    source_updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, campaign_id),
                    FOREIGN KEY(tenant_id, product_id)
                        REFERENCES product_ledger(tenant_id, product_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_campaign_metrics_product
                    ON campaign_metrics(tenant_id, product_id, start_date, end_date);
                CREATE TABLE IF NOT EXISTS inventory_movements (
                    movement_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    movement_date TEXT NOT NULL,
                    movement_type TEXT NOT NULL CHECK(movement_type IN ('initial', 'sale', 'refund', 'adjustment', 'restock')),
                    quantity_delta INTEGER NOT NULL,
                    ending_inventory INTEGER NOT NULL CHECK(ending_inventory >= 0),
                    source_type TEXT NOT NULL CHECK(source_type IN ('synthetic_demo', 'imported_file', 'platform_api')),
                    source_updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, product_id, movement_date, movement_type),
                    FOREIGN KEY(tenant_id, product_id)
                        REFERENCES product_ledger(tenant_id, product_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_inventory_movements_range
                    ON inventory_movements(tenant_id, product_id, movement_date);
                CREATE TABLE IF NOT EXISTS merchant_memories (
                    memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('candidate','active','inactive','conflicted')),
                    sensitivity TEXT NOT NULL CHECK(sensitivity IN ('public','internal','restricted')),
                    conflict_key TEXT,
                    valid_from TEXT,
                    valid_until TEXT,
                    confirmed_by TEXT,
                    confirmed_at TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_merchant_memory_recall
                    ON merchant_memories(tenant_id, status, scope, updated_at DESC);
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    summary_version INTEGER NOT NULL,
                    goals TEXT NOT NULL DEFAULT '[]',
                    decisions TEXT NOT NULL DEFAULT '[]',
                    open_items TEXT NOT NULL DEFAULT '[]',
                    product_refs TEXT NOT NULL DEFAULT '[]',
                    task_refs TEXT NOT NULL DEFAULT '[]',
                    source_turn_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, conversation_id),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS entity_memories (
                    entity_memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL CHECK(entity_type IN ('product','task','artifact')),
                    entity_id TEXT NOT NULL,
                    relation TEXT NOT NULL CHECK(relation IN ('active','recent','referenced')),
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, conversation_id, entity_type, entity_id),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_entity_memory_context
                    ON entity_memories(tenant_id, conversation_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS copilot_streams (
                    stream_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
                    response_payload TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_copilot_streams_tenant
                    ON copilot_streams(tenant_id, conversation_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS copilot_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stream_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    task_id TEXT,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(stream_id) REFERENCES copilot_streams(stream_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_copilot_events_resume
                    ON copilot_events(tenant_id, stream_id, event_id);
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    batch_job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    origin_turn_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0 CHECK(item_count >= 0),
                    completed_count INTEGER NOT NULL DEFAULT 0 CHECK(completed_count >= 0),
                    executed_count INTEGER NOT NULL DEFAULT 0 CHECK(executed_count >= 0),
                    failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    FOREIGN KEY(origin_turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_batch_jobs_conversation
                    ON batch_jobs(tenant_id, conversation_id, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_jobs_origin_operation
                    ON batch_jobs(tenant_id, origin_turn_id, operation);
                CREATE TABLE IF NOT EXISTS task_sessions (
                    task_session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    origin_turn_id TEXT NOT NULL,
                    batch_job_id TEXT,
                    intent TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active_workflow_run_id TEXT,
                    checkpoint_thread_id TEXT,
                    current_task_id TEXT,
                    entity_refs TEXT NOT NULL DEFAULT '[]',
                    state_version INTEGER NOT NULL DEFAULT 0 CHECK(state_version >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    FOREIGN KEY(origin_turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE,
                    FOREIGN KEY(batch_job_id) REFERENCES batch_jobs(batch_job_id) ON DELETE SET NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_task_sessions_current_task
                    ON task_sessions(tenant_id, current_task_id) WHERE current_task_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_task_sessions_conversation
                    ON task_sessions(tenant_id, conversation_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_task_sessions_batch
                    ON task_sessions(tenant_id, batch_job_id, created_at)
                    WHERE batch_job_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS batch_items (
                    tenant_id TEXT NOT NULL,
                    batch_job_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    task_session_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_payload TEXT NOT NULL,
                    task_id TEXT,
                    error_code TEXT,
                    execution_attempts INTEGER NOT NULL DEFAULT 0 CHECK(execution_attempts >= 0),
                    execution_history TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, batch_job_id, item_id),
                    UNIQUE(tenant_id, task_session_id),
                    FOREIGN KEY(batch_job_id) REFERENCES batch_jobs(batch_job_id) ON DELETE CASCADE,
                    FOREIGN KEY(task_session_id) REFERENCES task_sessions(task_session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    workflow_run_id TEXT PRIMARY KEY,
                    task_session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    trigger_turn_id TEXT NOT NULL,
                    task_id TEXT,
                    run_id TEXT,
                    parent_workflow_run_id TEXT,
                    status TEXT NOT NULL,
                    checkpoint_thread_id TEXT NOT NULL,
                    checkpoint_namespace TEXT NOT NULL DEFAULT 'default',
                    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
                    execution_epoch INTEGER NOT NULL DEFAULT 1 CHECK(execution_epoch >= 1),
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(task_session_id) REFERENCES task_sessions(task_session_id) ON DELETE CASCADE,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    FOREIGN KEY(trigger_turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE,
                    FOREIGN KEY(parent_workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE SET NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_runtime_run
                    ON workflow_runs(tenant_id, run_id) WHERE run_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_session
                    ON workflow_runs(tenant_id, task_session_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS turn_task_links (
                    tenant_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    task_session_id TEXT NOT NULL,
                    relation TEXT NOT NULL CHECK(relation IN ('created','continued','recalled','switched','batch_item')),
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, turn_id, task_session_id, relation),
                    FOREIGN KEY(turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE,
                    FOREIGN KEY(task_session_id) REFERENCES task_sessions(task_session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_turn_task_links_session
                    ON turn_task_links(tenant_id, task_session_id, linked_at);
                CREATE TABLE IF NOT EXISTS turn_task_routes (
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    turn_id TEXT PRIMARY KEY,
                    protocol_version TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_task_session_id TEXT,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    FOREIGN KEY(turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE,
                    FOREIGN KEY(target_task_session_id) REFERENCES task_sessions(task_session_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turn_task_routes_conversation
                    ON turn_task_routes(tenant_id, conversation_id, created_at);
                """
            )
            conversation_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "active_task_session_id" not in conversation_columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN active_task_session_id TEXT"
                )
            task_session_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(task_sessions)").fetchall()
            }
            if "checkpoint_thread_id" not in task_session_columns:
                connection.execute(
                    "ALTER TABLE task_sessions ADD COLUMN checkpoint_thread_id TEXT"
                )
            connection.execute(
                """UPDATE task_sessions SET checkpoint_thread_id=task_session_id
                WHERE checkpoint_thread_id IS NULL OR checkpoint_thread_id=''"""
            )
            batch_job_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(batch_jobs)").fetchall()
            }
            if "executed_count" not in batch_job_columns:
                connection.execute(
                    "ALTER TABLE batch_jobs ADD COLUMN executed_count INTEGER NOT NULL DEFAULT 0"
                )
            batch_item_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(batch_items)").fetchall()
            }
            if "execution_attempts" not in batch_item_columns:
                connection.execute(
                    "ALTER TABLE batch_items ADD COLUMN execution_attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "execution_history" not in batch_item_columns:
                connection.execute(
                    "ALTER TABLE batch_items ADD COLUMN execution_history TEXT NOT NULL DEFAULT '[]'"
                )
            turn_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(turns)").fetchall()
            }
            if "response_payload" not in turn_columns:
                connection.execute("ALTER TABLE turns ADD COLUMN response_payload TEXT")
            product_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(product_ledger)").fetchall()
            }
            if "resource_version" not in product_columns:
                connection.execute(
                    "ALTER TABLE product_ledger ADD COLUMN resource_version INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(2, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(3, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(4, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(5, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(6, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(7, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(8, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(9, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(10, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(11, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(12, ?)",
                (_now(),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(13, ?)",
                (_now(),),
            )

    def create_conversation(self, tenant_id: str, *, title: str = "新会话") -> ConversationRecord:
        conversation_id = f"conv_{uuid4().hex[:12]}"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO conversations(
                    conversation_id, tenant_id, title, status, created_at, updated_at
                ) VALUES(?, ?, ?, 'active', ?, ?)""",
                (conversation_id, tenant_id, _title(title), now, now),
            )
        return self.get_conversation(tenant_id, conversation_id)

    def get_conversation(self, tenant_id: str, conversation_id: str) -> ConversationRecord:
        _validate_id(conversation_id, "conversation_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND conversation_id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Conversation not found")
        return _conversation(row)

    def set_active_product(
        self, tenant_id: str, conversation_id: str, product_id: str
    ) -> None:
        """Update the conversation pointer after a tenant-scoped resolver succeeds."""

        now = _now()
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE conversations SET active_product_id=?, updated_at=?
                WHERE tenant_id=? AND conversation_id=?""",
                (product_id, now, tenant_id, conversation_id),
            ).rowcount
        if changed != 1:
            raise ConversationNotFoundError("Conversation not found")

    def set_active_task_session(
        self,
        tenant_id: str,
        conversation_id: str,
        task_session_id: str | None,
    ) -> None:
        """Move the conversation cursor without mutating another task's state."""

        if task_session_id is not None:
            session = self.get_task_session(tenant_id, task_session_id)
            if session.conversation_id != conversation_id:
                raise ConversationConflictError(
                    "Task session and conversation do not match"
                )
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE conversations SET active_task_session_id=?, updated_at=?
                WHERE tenant_id=? AND conversation_id=?""",
                (task_session_id, _now(), tenant_id, conversation_id),
            ).rowcount
        if changed != 1:
            raise ConversationNotFoundError("Conversation not found")

    def list_conversations(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        query: str | None = None,
        product_id: str | None = None,
        approval_status: str = "all",
    ) -> list[ConversationSummary]:
        where = ["c.tenant_id = ?"]
        parameters: list[object] = [tenant_id]
        if query and query.strip():
            needle = f"%{query.strip()}%"
            where.append(
                "(c.title LIKE ? OR EXISTS (SELECT 1 FROM messages sm "
                "WHERE sm.tenant_id=c.tenant_id AND sm.conversation_id=c.conversation_id "
                "AND sm.content LIKE ?))"
            )
            parameters.extend((needle, needle))
        if product_id:
            where.append(
                "(c.active_product_id = ? OR EXISTS (SELECT 1 FROM task_product_links tpl "
                "WHERE tpl.tenant_id=c.tenant_id AND tpl.conversation_id=c.conversation_id "
                "AND tpl.product_id=?))"
            )
            parameters.extend((product_id, product_id))
        if approval_status == "pending":
            where.append(
                "(SELECT outcome FROM task_index ti2 WHERE ti2.tenant_id=c.tenant_id "
                "AND ti2.conversation_id=c.conversation_id ORDER BY ti2.updated_at DESC LIMIT 1) "
                "= 'awaiting_approval'"
            )
        parameters.append(max(1, min(limit, 100)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT c.*,
                    COALESCE((SELECT content FROM messages m
                        WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.conversation_id
                        ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1), '') AS last_message,
                    (SELECT outcome FROM task_index ti
                        WHERE ti.tenant_id = c.tenant_id AND ti.conversation_id = c.conversation_id
                        ORDER BY ti.updated_at DESC LIMIT 1) AS last_task_status,
                    (SELECT COUNT(*) FROM messages m
                        WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.conversation_id) AS message_count
                FROM conversations c
                WHERE {' AND '.join(where)}
                ORDER BY c.updated_at DESC
                LIMIT ?""",
                tuple(parameters),
            ).fetchall()
        return [ConversationSummary(**_conversation(row).model_dump(), last_message=row["last_message"], last_task_status=row["last_task_status"], message_count=row["message_count"]) for row in rows]

    def get_detail(self, tenant_id: str, conversation_id: str) -> ConversationDetail:
        conversation = self.get_conversation(tenant_id, conversation_id)
        with self._connect() as connection:
            messages = connection.execute(
                "SELECT * FROM messages WHERE tenant_id = ? AND conversation_id = ? ORDER BY created_at, rowid",
                (tenant_id, conversation_id),
            ).fetchall()
            turns = connection.execute(
                "SELECT * FROM turns WHERE tenant_id = ? AND conversation_id = ? ORDER BY ordinal",
                (tenant_id, conversation_id),
            ).fetchall()
            tasks = connection.execute(
                "SELECT * FROM task_index WHERE tenant_id = ? AND conversation_id = ? ORDER BY created_at",
                (tenant_id, conversation_id),
            ).fetchall()
            task_sessions = connection.execute(
                """SELECT * FROM task_sessions WHERE tenant_id=? AND conversation_id=?
                ORDER BY created_at""",
                (tenant_id, conversation_id),
            ).fetchall()
            workflow_runs = connection.execute(
                """SELECT * FROM workflow_runs WHERE tenant_id=? AND conversation_id=?
                ORDER BY started_at""",
                (tenant_id, conversation_id),
            ).fetchall()
            batch_jobs = connection.execute(
                """SELECT * FROM batch_jobs WHERE tenant_id=? AND conversation_id=?
                ORDER BY created_at""",
                (tenant_id, conversation_id),
            ).fetchall()
            batch_items = connection.execute(
                """SELECT bi.* FROM batch_items bi
                JOIN batch_jobs bj ON bj.batch_job_id=bi.batch_job_id
                WHERE bi.tenant_id=? AND bj.conversation_id=?
                ORDER BY bi.created_at, bi.item_id""",
                (tenant_id, conversation_id),
            ).fetchall()
        return ConversationDetail(
            conversation=conversation,
            messages=[_message(row) for row in messages],
            turns=[_turn(row) for row in turns],
            tasks=[_task(row) for row in tasks],
            task_sessions=[_task_session(row) for row in task_sessions],
            workflow_runs=[_workflow_run(row) for row in workflow_runs],
            batch_jobs=[_batch_job(row) for row in batch_jobs],
            batch_items=[_batch_item(row) for row in batch_items],
        )

    def create_batch_job(
        self,
        tenant_id: str,
        conversation_id: str,
        origin_turn_id: str,
        *,
        operation: str,
        item_count: int = 0,
    ) -> BatchJobRecord:
        """Create a durable parent for a future multi-item business request."""

        if item_count < 0:
            raise ConversationStoreError("item_count must not be negative")
        self.get_conversation(tenant_id, conversation_id)
        self._get_turn(tenant_id, origin_turn_id)
        batch_job_id = f"batch_{uuid4().hex[:12]}"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO batch_jobs(
                    batch_job_id, tenant_id, conversation_id, origin_turn_id, operation,
                    status, item_count, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, 'created', ?, ?, ?)""",
                (
                    batch_job_id,
                    tenant_id,
                    conversation_id,
                    origin_turn_id,
                    operation,
                    item_count,
                    now,
                    now,
                ),
            )
        return self.get_batch_job(tenant_id, batch_job_id)

    def get_batch_job(self, tenant_id: str, batch_job_id: str) -> BatchJobRecord:
        _validate_id(batch_job_id, "batch_job_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM batch_jobs WHERE tenant_id=? AND batch_job_id=?",
                (tenant_id, batch_job_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Batch job not found")
        return _batch_job(row)

    def materialize_batch_plan(
        self,
        tenant_id: str,
        conversation_id: str,
        origin_turn_id: str,
        *,
        operation: str,
        items: list[dict],
    ) -> tuple[BatchJobRecord, list[BatchItemRecord]]:
        """Atomically create one durable child task per compiled product item."""

        if not 2 <= len(items) <= 5:
            raise ConversationStoreError("A batch plan must contain 2 to 5 items")
        self.get_conversation(tenant_id, conversation_id)
        self._get_turn(tenant_id, origin_turn_id)
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT batch_job_id FROM batch_jobs
                WHERE tenant_id=? AND origin_turn_id=? AND operation=?""",
                (tenant_id, origin_turn_id, operation),
            ).fetchone()
            batch_job_id = row["batch_job_id"] if row else f"batch_{uuid4().hex[:12]}"
            if row is None:
                connection.execute(
                    """INSERT INTO batch_jobs(
                        batch_job_id, tenant_id, conversation_id, origin_turn_id,
                        operation, status, item_count, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, 'created', ?, ?, ?)""",
                    (
                        batch_job_id, tenant_id, conversation_id, origin_turn_id,
                        operation, len(items), now, now,
                    ),
                )
            for item in items:
                item_id = str(item["item_id"])
                exists = connection.execute(
                    """SELECT task_session_id FROM batch_items
                    WHERE tenant_id=? AND batch_job_id=? AND item_id=?""",
                    (tenant_id, batch_job_id, item_id),
                ).fetchone()
                if exists is not None:
                    continue
                task_session_id = f"session_{uuid4().hex[:12]}"
                label = str(item.get("label") or item_id)
                request_payload = dict(item.get("structured_request") or {})
                item_status = str(item.get("status") or "created")
                connection.execute(
                    """INSERT INTO task_sessions(
                        task_session_id, tenant_id, conversation_id, origin_turn_id,
                        batch_job_id, intent, title, status, checkpoint_thread_id,
                        created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, 'create_listing', ?, ?, ?, ?, ?)""",
                    (
                        task_session_id, tenant_id, conversation_id, origin_turn_id,
                        batch_job_id, _title(f"批量上架：{label}"),
                        "waiting_for_input" if item_status == "waiting_for_input" else "created",
                        task_session_id, now, now,
                    ),
                )
                self._link_turn_task(
                    connection,
                    tenant_id=tenant_id,
                    turn_id=origin_turn_id,
                    task_session_id=task_session_id,
                    relation="batch_item",
                    linked_at=now,
                )
                connection.execute(
                    """INSERT INTO batch_items(
                        tenant_id, batch_job_id, item_id, task_session_id, label,
                        status, request_payload, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tenant_id, batch_job_id, item_id, task_session_id, label,
                        item_status, json.dumps(request_payload, ensure_ascii=False), now, now,
                    ),
                )
            connection.execute(
                """UPDATE batch_jobs SET item_count=(
                    SELECT COUNT(*) FROM batch_items
                    WHERE tenant_id=? AND batch_job_id=?
                ), updated_at=? WHERE tenant_id=? AND batch_job_id=?""",
                (tenant_id, batch_job_id, now, tenant_id, batch_job_id),
            )
            connection.commit()
        return self.get_batch_job(tenant_id, batch_job_id), self.list_batch_items(
            tenant_id, batch_job_id
        )

    def list_batch_items(
        self, tenant_id: str, batch_job_id: str
    ) -> list[BatchItemRecord]:
        self.get_batch_job(tenant_id, batch_job_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM batch_items WHERE tenant_id=? AND batch_job_id=?
                ORDER BY created_at, item_id""",
                (tenant_id, batch_job_id),
            ).fetchall()
        return [_batch_item(row) for row in rows]

    def mark_batch_item_running(
        self, tenant_id: str, batch_job_id: str, item_id: str
    ) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE batch_items SET status='running', error_code=NULL, updated_at=?
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?
                  AND status IN ('created', 'ready', 'failed', 'running')""",
                (now, tenant_id, batch_job_id, item_id),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise ConversationConflictError("Batch item is not runnable")
            connection.execute(
                """UPDATE batch_jobs SET status='running', updated_at=?
                WHERE tenant_id=? AND batch_job_id=?""",
                (now, tenant_id, batch_job_id),
            )
            connection.commit()

    def record_batch_item_state(
        self,
        state: TaskState,
        *,
        batch_job_id: str,
        item_id: str,
        task_session_id: str,
        status: str,
    ) -> None:
        """Bind one child TaskState to its pre-created batch task identity."""

        if status not in {"awaiting_approval", "completed"}:
            raise ConversationStoreError("Unsupported successful batch item status")
        if not state.conversation_id or not state.turn_id:
            raise ConversationStoreError("Batch child is missing conversation metadata")
        tenant_id = state.principal.tenant_id
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            owner = connection.execute(
                """SELECT 1 FROM batch_items
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?
                  AND task_session_id=?""",
                (tenant_id, batch_job_id, item_id, task_session_id),
            ).fetchone()
            if owner is None:
                connection.rollback()
                raise ConversationNotFoundError("Batch item task identity not found")
            connection.execute(
                """INSERT INTO task_index(
                    task_id, conversation_id, turn_id, tenant_id, intent, outcome,
                    run_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    outcome=excluded.outcome, run_id=excluded.run_id,
                    updated_at=excluded.updated_at""",
                (
                    state.task_id, state.conversation_id, state.turn_id, tenant_id,
                    state.intent, state.outcome.value, state.run_id, now, now,
                ),
            )
            self._mirror_task_state(
                connection,
                state,
                now,
                preferred_task_session_id=task_session_id,
            )
            connection.execute(
                """UPDATE batch_items SET status=?, task_id=?, error_code=NULL,
                    updated_at=?
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?""",
                (status, state.task_id, now, tenant_id, batch_job_id, item_id),
            )
            connection.commit()

    def record_batch_item_failure(
        self,
        tenant_id: str,
        batch_job_id: str,
        item_id: str,
        *,
        error_code: str,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE batch_items SET status='failed', error_code=?, updated_at=?
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?""",
                (error_code, now, tenant_id, batch_job_id, item_id),
            ).rowcount
            if changed != 1:
                raise ConversationNotFoundError("Batch item not found")

    def record_batch_item_skipped(
        self,
        tenant_id: str,
        batch_job_id: str,
        item_id: str,
        *,
        error_code: str,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE batch_items SET status='skipped', error_code=?, updated_at=?
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?
                  AND status='waiting_for_input'""",
                (error_code, now, tenant_id, batch_job_id, item_id),
            ).rowcount
            if changed != 1:
                raise ConversationConflictError("Batch item is not skippable")

    def finalize_batch_job(
        self, tenant_id: str, batch_job_id: str, *, status: str
    ) -> BatchJobRecord:
        if status not in {"awaiting_approval", "partially_completed", "failed"}:
            raise ConversationStoreError("Unsupported batch aggregate status")
        now = _now()
        with self._connect() as connection:
            counts = connection.execute(
                """SELECT
                    SUM(CASE WHEN status IN ('awaiting_approval', 'completed') THEN 1 ELSE 0 END) AS successful,
                    SUM(CASE WHEN status IN ('failed', 'skipped') THEN 1 ELSE 0 END) AS failed
                FROM batch_items WHERE tenant_id=? AND batch_job_id=?""",
                (tenant_id, batch_job_id),
            ).fetchone()
            changed = connection.execute(
                """UPDATE batch_jobs SET status=?, completed_count=?, failed_count=?,
                    updated_at=?, completed_at=?
                WHERE tenant_id=? AND batch_job_id=?""",
                (
                    status, int(counts["successful"] or 0), int(counts["failed"] or 0),
                    now, now, tenant_id, batch_job_id,
                ),
            ).rowcount
            if changed != 1:
                raise ConversationNotFoundError("Batch job not found")
        return self.get_batch_job(tenant_id, batch_job_id)

    def claim_batch_item_execution(
        self, tenant_id: str, batch_job_id: str, item_id: str
    ) -> BatchItemRecord:
        """Atomically claim one approved child so duplicate requests cannot write twice."""

        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE batch_items SET status='running', error_code=NULL,
                    execution_attempts=execution_attempts+1, updated_at=?
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?
                  AND status='awaiting_approval' AND task_id IS NOT NULL
                  AND execution_attempts < 3""",
                (now, tenant_id, batch_job_id, item_id),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise ConversationConflictError(
                    "Batch item is not awaiting approval or is already executing"
                )
            connection.execute(
                """UPDATE batch_jobs SET status='running', updated_at=?
                WHERE tenant_id=? AND batch_job_id=?""",
                (now, tenant_id, batch_job_id),
            )
            connection.commit()
        return next(
            item
            for item in self.list_batch_items(tenant_id, batch_job_id)
            if item.item_id == item_id
        )

    def record_batch_item_execution(
        self,
        tenant_id: str,
        batch_job_id: str,
        item_id: str,
        *,
        completed: bool,
        error_code: str | None = None,
    ) -> None:
        now = _now()
        status = "completed" if completed else "needs_attention"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT execution_attempts, execution_history FROM batch_items
                WHERE tenant_id=? AND batch_job_id=? AND item_id=? AND status='running'""",
                (tenant_id, batch_job_id, item_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ConversationConflictError("Batch item execution claim was lost")
            history = json.loads(row["execution_history"] or "[]")
            history.append(
                {
                    "attempt": int(row["execution_attempts"]),
                    "status": status,
                    "error_code": error_code,
                    "occurred_at": now,
                }
            )
            changed = connection.execute(
                """UPDATE batch_items SET status=?, error_code=?, execution_history=?, updated_at=?
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?
                  AND status='running'""",
                (
                    status,
                    error_code,
                    json.dumps(history, ensure_ascii=False),
                    now,
                    tenant_id,
                    batch_job_id,
                    item_id,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise ConversationConflictError("Batch item execution claim was lost")
            connection.commit()

    def prepare_batch_item_retry(
        self,
        tenant_id: str,
        batch_job_id: str,
        item_id: str,
        *,
        max_attempts: int = 3,
    ) -> BatchItemRecord:
        """Move a known failed write back to approval without erasing its audit."""

        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE batch_items SET status='awaiting_approval', updated_at=?
                WHERE tenant_id=? AND batch_job_id=? AND item_id=?
                  AND status='needs_attention' AND execution_attempts < ?""",
                (now, tenant_id, batch_job_id, item_id, max_attempts),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise ConversationConflictError(
                    "Batch item is not retryable or exhausted its execution attempts"
                )
            connection.execute(
                """UPDATE batch_jobs SET status='running', completed_at=NULL, updated_at=?
                WHERE tenant_id=? AND batch_job_id=?""",
                (now, tenant_id, batch_job_id),
            )
            connection.commit()
        return next(
            item
            for item in self.list_batch_items(tenant_id, batch_job_id)
            if item.item_id == item_id
        )

    def finalize_batch_execution(
        self, tenant_id: str, batch_job_id: str
    ) -> BatchJobRecord:
        """Recompute the parent from durable child states after selected writes finish."""

        now = _now()
        with self._connect() as connection:
            counts = connection.execute(
                """SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS executed,
                    SUM(CASE WHEN status IN ('failed','skipped','needs_attention') THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status='awaiting_approval' THEN 1 ELSE 0 END) AS waiting,
                    SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running
                FROM batch_items WHERE tenant_id=? AND batch_job_id=?""",
                (tenant_id, batch_job_id),
            ).fetchone()
            total = int(counts["total"] or 0)
            executed = int(counts["executed"] or 0)
            failed = int(counts["failed"] or 0)
            waiting = int(counts["waiting"] or 0)
            running = int(counts["running"] or 0)
            if running:
                status = "running"
            elif executed == total and total:
                status = "completed"
            elif failed and executed:
                status = "partially_completed"
            elif failed and not waiting:
                status = "failed"
            else:
                status = "awaiting_approval"
            changed = connection.execute(
                """UPDATE batch_jobs SET status=?, executed_count=?, failed_count=?,
                    updated_at=?, completed_at=?
                WHERE tenant_id=? AND batch_job_id=?""",
                (
                    status,
                    executed,
                    failed,
                    now,
                    now if status in {"completed", "partially_completed", "failed"} else None,
                    tenant_id,
                    batch_job_id,
                ),
            ).rowcount
            if changed != 1:
                raise ConversationNotFoundError("Batch job not found")
        return self.get_batch_job(tenant_id, batch_job_id)

    def update_batch_response_snapshot(
        self,
        tenant_id: str,
        batch_job_id: str,
        *,
        response_payload: dict,
        assistant_message: str,
    ) -> None:
        """Persist the aggregate action so reloads do not resurrect stale approvals."""

        batch = self.get_batch_job(tenant_id, batch_job_id)
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE turns SET response_payload=?
                WHERE tenant_id=? AND conversation_id=? AND turn_id=?""",
                (
                    json.dumps(response_payload, ensure_ascii=False),
                    tenant_id,
                    batch.conversation_id,
                    batch.origin_turn_id,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise ConversationNotFoundError("Batch origin turn not found")
            exists = connection.execute(
                """SELECT 1 FROM messages WHERE tenant_id=? AND turn_id=?
                  AND role='assistant' AND content=?""",
                (tenant_id, batch.origin_turn_id, assistant_message),
            ).fetchone()
            if exists is None:
                connection.execute(
                    """INSERT INTO messages(
                        message_id, conversation_id, turn_id, tenant_id, role,
                        content, intent, product_refs, created_at
                    ) VALUES(?, ?, ?, ?, 'assistant', ?, 'batch_execution', '[]', ?)""",
                    (
                        f"msg_{uuid4().hex[:12]}",
                        batch.conversation_id,
                        batch.origin_turn_id,
                        tenant_id,
                        assistant_message,
                        now,
                    ),
                )
            connection.execute(
                """UPDATE conversations SET updated_at=?
                WHERE tenant_id=? AND conversation_id=?""",
                (now, tenant_id, batch.conversation_id),
            )
            connection.commit()

    def create_task_session(
        self,
        tenant_id: str,
        conversation_id: str,
        origin_turn_id: str,
        *,
        intent: str,
        title: str,
        batch_job_id: str | None = None,
        relation: str = "created",
    ) -> TaskSessionRecord:
        """Create a task identity without starting a workflow run."""

        self.get_conversation(tenant_id, conversation_id)
        self._get_turn(tenant_id, origin_turn_id)
        if batch_job_id:
            batch = self.get_batch_job(tenant_id, batch_job_id)
            if batch.conversation_id != conversation_id:
                raise ConversationConflictError(
                    "Batch job and task session must belong to the same conversation"
                )
        task_session_id = f"session_{uuid4().hex[:12]}"
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO task_sessions(
                    task_session_id, tenant_id, conversation_id, origin_turn_id,
                    batch_job_id, intent, title, status, checkpoint_thread_id,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)""",
                (
                    task_session_id,
                    tenant_id,
                    conversation_id,
                    origin_turn_id,
                    batch_job_id,
                    intent,
                    _title(title),
                    task_session_id,
                    now,
                    now,
                ),
            )
            self._link_turn_task(
                connection,
                tenant_id=tenant_id,
                turn_id=origin_turn_id,
                task_session_id=task_session_id,
                relation=relation,
                linked_at=now,
            )
            if batch_job_id:
                connection.execute(
                    """UPDATE batch_jobs SET item_count=(
                        SELECT COUNT(*) FROM task_sessions
                        WHERE tenant_id=? AND batch_job_id=?
                    ), updated_at=? WHERE tenant_id=? AND batch_job_id=?""",
                    (tenant_id, batch_job_id, now, tenant_id, batch_job_id),
                )
            connection.commit()
        return self.get_task_session(tenant_id, task_session_id)

    def get_task_session(
        self, tenant_id: str, task_session_id: str
    ) -> TaskSessionRecord:
        _validate_id(task_session_id, "task_session_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_sessions WHERE tenant_id=? AND task_session_id=?",
                (tenant_id, task_session_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Task session not found")
        return _task_session(row)

    def list_task_sessions(
        self, tenant_id: str, conversation_id: str
    ) -> list[TaskSessionRecord]:
        self.get_conversation(tenant_id, conversation_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM task_sessions WHERE tenant_id=? AND conversation_id=?
                ORDER BY updated_at DESC""",
                (tenant_id, conversation_id),
            ).fetchall()
        return [_task_session(row) for row in rows]

    def list_workflow_runs(
        self, tenant_id: str, task_session_id: str
    ) -> list[WorkflowRunRecord]:
        self.get_task_session(tenant_id, task_session_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM workflow_runs WHERE tenant_id=? AND task_session_id=?
                ORDER BY started_at""",
                (tenant_id, task_session_id),
            ).fetchall()
        return [_workflow_run(row) for row in rows]

    def list_turn_task_links(
        self, tenant_id: str, task_session_id: str
    ) -> list[TurnTaskLinkRecord]:
        self.get_task_session(tenant_id, task_session_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM turn_task_links WHERE tenant_id=? AND task_session_id=?
                ORDER BY linked_at""",
                (tenant_id, task_session_id),
            ).fetchall()
        return [TurnTaskLinkRecord(**dict(row)) for row in rows]

    def link_turn_to_task(
        self,
        tenant_id: str,
        turn_id: str,
        task_session_id: str,
        *,
        relation: str,
    ) -> None:
        self.get_task_session(tenant_id, task_session_id)
        self._get_turn(tenant_id, turn_id)
        with self._connect() as connection:
            self._link_turn_task(
                connection,
                tenant_id=tenant_id,
                turn_id=turn_id,
                task_session_id=task_session_id,
                relation=relation,
                linked_at=_now(),
            )

    def update_task_session(
        self,
        tenant_id: str,
        task_session_id: str,
        *,
        status: str | None = None,
        intent: str | None = None,
        current_task_id: str | None = None,
    ) -> TaskSessionRecord:
        updates = ["updated_at=?"]
        values: list[object] = [_now()]
        if status is not None:
            updates.append("status=?")
            values.append(status)
            if status in _TERMINAL_TASK_SESSION_STATUSES:
                updates.append("completed_at=?")
                values.append(_now())
        if intent is not None:
            updates.append("intent=?")
            values.append(intent)
        if current_task_id is not None:
            updates.append("current_task_id=?")
            values.append(current_task_id)
        values.extend((tenant_id, task_session_id))
        with self._connect() as connection:
            changed = connection.execute(
                f"UPDATE task_sessions SET {', '.join(updates)} "
                "WHERE tenant_id=? AND task_session_id=?",
                tuple(values),
            ).rowcount
        if changed != 1:
            raise ConversationNotFoundError("Task session not found")
        return self.get_task_session(tenant_id, task_session_id)

    def record_task_route(
        self,
        tenant_id: str,
        conversation_id: str,
        turn_id: str,
        decision: TaskRouteDecision,
    ) -> TurnTaskRouteRecord:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO turn_task_routes(
                    tenant_id, conversation_id, turn_id, protocol_version, relation,
                    target_task_session_id, confidence, reason, evidence, source, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    conversation_id,
                    turn_id,
                    decision.protocol_version,
                    decision.relation,
                    decision.target_task_session_id,
                    decision.confidence,
                    decision.reason,
                    json.dumps(decision.evidence, ensure_ascii=False),
                    decision.source,
                    now,
                ),
            )
        return TurnTaskRouteRecord(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            created_at=now,
            **decision.model_dump(),
        )

    def list_task_routes(
        self, tenant_id: str, conversation_id: str
    ) -> list[TurnTaskRouteRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM turn_task_routes
                WHERE tenant_id=? AND conversation_id=? ORDER BY created_at""",
                (tenant_id, conversation_id),
            ).fetchall()
        return [
            TurnTaskRouteRecord(
                tenant_id=row["tenant_id"],
                conversation_id=row["conversation_id"],
                turn_id=row["turn_id"],
                protocol_version=row["protocol_version"],
                relation=row["relation"],
                target_task_session_id=row["target_task_session_id"],
                confidence=row["confidence"],
                reason=row["reason"],
                evidence=json.loads(row["evidence"] or "[]"),
                source=row["source"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def begin_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        client_request_id: str,
        message: str,
        intent: str = "create_listing",
    ) -> TurnReservation:
        _validate_id(client_request_id, "client_request_id")
        conversation = self.get_conversation(tenant_id, conversation_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT * FROM turns WHERE tenant_id = ? AND client_request_id = ?",
                (tenant_id, client_request_id),
            ).fetchone()
            if duplicate is not None:
                stored = connection.execute(
                    "SELECT content FROM messages WHERE tenant_id = ? AND turn_id = ? AND role = 'user'",
                    (tenant_id, duplicate["turn_id"]),
                ).fetchone()
                connection.commit()
                if stored is None or stored["content"] != message:
                    raise ConversationConflictError(
                        "client_request_id was already used for different content"
                    )
                return TurnReservation(created=False, conversation=conversation, turn=_turn(duplicate))
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM turns WHERE tenant_id = ? AND conversation_id = ?",
                (tenant_id, conversation_id),
            ).fetchone()[0]
            turn_id = f"turn_{uuid4().hex[:12]}"
            message_id = f"msg_{uuid4().hex[:12]}"
            now = _now()
            connection.execute(
                """INSERT INTO turns(
                    turn_id, conversation_id, tenant_id, ordinal, client_request_id, status, created_at
                ) VALUES(?, ?, ?, ?, ?, 'processing', ?)""",
                (turn_id, conversation_id, tenant_id, ordinal, client_request_id, now),
            )
            connection.execute(
                """INSERT INTO messages(
                    message_id, conversation_id, turn_id, tenant_id, role, content, intent, created_at
                ) VALUES(?, ?, ?, ?, 'user', ?, ?, ?)""",
                (message_id, conversation_id, turn_id, tenant_id, message, intent, now),
            )
            connection.execute(
                "UPDATE conversations SET title = CASE WHEN ? = 1 THEN ? ELSE title END, updated_at = ? WHERE tenant_id = ? AND conversation_id = ?",
                (ordinal, _title(message), now, tenant_id, conversation_id),
            )
            connection.commit()
        return TurnReservation(
            created=True,
            conversation=self.get_conversation(tenant_id, conversation_id),
            turn=self._get_turn(tenant_id, turn_id),
        )

    def complete_turn(
        self,
        state: TaskState,
        assistant_message: str,
        *,
        response_payload: dict | None = None,
    ) -> None:
        if not state.conversation_id or not state.turn_id:
            raise ConversationStoreError("Task is missing conversation metadata")
        tenant_id = state.principal.tenant_id
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE turns SET status = 'completed', task_id = ?, completed_at = ?, response_payload = ?
                WHERE tenant_id = ? AND conversation_id = ? AND turn_id = ?""",
                (
                    state.task_id,
                    now,
                    json.dumps(response_payload, ensure_ascii=False) if response_payload else None,
                    tenant_id,
                    state.conversation_id,
                    state.turn_id,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise ConversationNotFoundError("Turn not found")
            exists = connection.execute(
                "SELECT 1 FROM messages WHERE tenant_id = ? AND turn_id = ? AND role = 'assistant'",
                (tenant_id, state.turn_id),
            ).fetchone()
            if exists is None:
                connection.execute(
                    """INSERT INTO messages(
                        message_id, conversation_id, turn_id, tenant_id, role, content,
                        intent, task_id, product_refs, created_at
                    ) VALUES(?, ?, ?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                    (
                        f"msg_{uuid4().hex[:12]}", state.conversation_id, state.turn_id,
                        tenant_id, assistant_message, state.intent, state.task_id,
                        json.dumps(state.entity_refs, ensure_ascii=False), now,
                    ),
                )
            connection.execute(
                """INSERT INTO task_index(
                    task_id, conversation_id, turn_id, tenant_id, intent, outcome, run_id, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    outcome = excluded.outcome, run_id = excluded.run_id, updated_at = excluded.updated_at""",
                (
                    state.task_id, state.conversation_id, state.turn_id, tenant_id,
                    state.intent, state.outcome.value, state.run_id, now, now,
                ),
            )
            self._mirror_task_state(connection, state, now)
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE tenant_id = ? AND conversation_id = ?",
                (now, tenant_id, state.conversation_id),
            )
            connection.commit()

    def complete_message_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        turn_id: str,
        *,
        intent: str,
        assistant_message: str,
        response_payload: dict,
        task_id: str | None = None,
        product_refs: list[str] | None = None,
    ) -> None:
        """Complete a non-workflow turn such as clarification or general chat."""

        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE turns SET status='completed', task_id=?, completed_at=?, response_payload=?
                WHERE tenant_id=? AND conversation_id=? AND turn_id=?""",
                (
                    task_id,
                    now,
                    json.dumps(response_payload, ensure_ascii=False),
                    tenant_id,
                    conversation_id,
                    turn_id,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise ConversationNotFoundError("Turn not found")
            connection.execute(
                """INSERT INTO messages(
                    message_id, conversation_id, turn_id, tenant_id, role, content,
                    intent, task_id, product_refs, created_at
                ) VALUES(?, ?, ?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                (
                    f"msg_{uuid4().hex[:12]}",
                    conversation_id,
                    turn_id,
                    tenant_id,
                    assistant_message,
                    intent,
                    task_id,
                    json.dumps(product_refs or [], ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at=? WHERE tenant_id=? AND conversation_id=?",
                (now, tenant_id, conversation_id),
            )
            connection.commit()

    def response_for_turn(self, tenant_id: str, turn_id: str) -> dict | None:
        turn = self._get_turn(tenant_id, turn_id)
        return turn.response_payload

    def save_pending_request(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        compiled_payload: dict,
        clarification_round: int,
        last_question: str,
        status: str = "waiting_for_input",
        task_session_id: str | None = None,
        checkpoint_thread_id: str | None = None,
    ) -> None:
        self.get_conversation(tenant_id, conversation_id)
        now = _now()
        if task_session_id is not None:
            session = self.get_task_session(tenant_id, task_session_id)
            if session.conversation_id != conversation_id:
                raise ConversationConflictError(
                    "Pending request and task session do not match"
                )
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO task_pending_requests(
                        task_session_id, conversation_id, tenant_id,
                        checkpoint_thread_id, status, compiled_payload,
                        clarification_round, last_question, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_session_id) DO UPDATE SET
                        checkpoint_thread_id=excluded.checkpoint_thread_id,
                        status=excluded.status,
                        compiled_payload=excluded.compiled_payload,
                        clarification_round=excluded.clarification_round,
                        last_question=excluded.last_question,
                        updated_at=excluded.updated_at""",
                    (
                        task_session_id,
                        conversation_id,
                        tenant_id,
                        checkpoint_thread_id or conversation_id,
                        status,
                        json.dumps(compiled_payload, ensure_ascii=False),
                        clarification_round,
                        last_question,
                        now,
                        now,
                    ),
                )
            return
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO pending_requests(
                    conversation_id, tenant_id, status, compiled_payload,
                    clarification_round, last_question, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    status=excluded.status,
                    compiled_payload=excluded.compiled_payload,
                    clarification_round=excluded.clarification_round,
                    last_question=excluded.last_question,
                    updated_at=excluded.updated_at""",
                (
                    conversation_id,
                    tenant_id,
                    status,
                    json.dumps(compiled_payload, ensure_ascii=False),
                    clarification_round,
                    last_question,
                    now,
                    now,
                ),
            )

    def get_pending_request(
        self, tenant_id: str, conversation_id: str
    ) -> PendingRequestRecord | None:
        conversation = self.get_conversation(tenant_id, conversation_id)
        if conversation.active_task_session_id:
            pending = self.get_task_pending_request(
                tenant_id, conversation.active_task_session_id
            )
            if pending is not None and pending.status == "waiting_for_input":
                return pending
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM pending_requests
                WHERE tenant_id=? AND conversation_id=? AND status='waiting_for_input'""",
                (tenant_id, conversation_id),
            ).fetchone()
            if row is not None:
                # Compatibility cleanup for turns created before pending requests
                # became single-use. A later failed turn means the clarification
                # was already attempted; retaining it would bind an unrelated new
                # message to an obsolete LangGraph interrupt.
                failed_reply = connection.execute(
                    """SELECT 1 FROM turns
                    WHERE tenant_id=? AND conversation_id=? AND status='failed'
                    AND created_at>? LIMIT 1""",
                    (tenant_id, conversation_id, row["updated_at"]),
                ).fetchone()
                if failed_reply is not None:
                    connection.execute(
                        "DELETE FROM pending_requests WHERE tenant_id=? AND conversation_id=?",
                        (tenant_id, conversation_id),
                    )
                    row = None
        if row is None:
            return None
        return PendingRequestRecord(
            conversation_id=row["conversation_id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            compiled_payload=json.loads(row["compiled_payload"]),
            clarification_round=row["clarification_round"],
            last_question=row["last_question"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_task_pending_request(
        self, tenant_id: str, task_session_id: str
    ) -> PendingRequestRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM task_pending_requests
                WHERE tenant_id=? AND task_session_id=?
                AND status IN ('waiting_for_input','suspended')""",
                (tenant_id, task_session_id),
            ).fetchone()
        if row is None:
            return None
        return PendingRequestRecord(
            conversation_id=row["conversation_id"],
            tenant_id=row["tenant_id"],
            task_session_id=row["task_session_id"],
            checkpoint_thread_id=row["checkpoint_thread_id"],
            status=row["status"],
            compiled_payload=json.loads(row["compiled_payload"]),
            clarification_round=row["clarification_round"],
            last_question=row["last_question"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_task_pending_status(
        self, tenant_id: str, task_session_id: str, status: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE task_pending_requests SET status=?, updated_at=?
                WHERE tenant_id=? AND task_session_id=?""",
                (status, _now(), tenant_id, task_session_id),
            )

    def clear_pending_request(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        task_session_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            if task_session_id is not None:
                connection.execute(
                    "DELETE FROM task_pending_requests WHERE tenant_id=? AND task_session_id=?",
                    (tenant_id, task_session_id),
                )
                return
            connection.execute(
                "DELETE FROM pending_requests WHERE tenant_id=? AND conversation_id=?",
                (tenant_id, conversation_id),
            )

    def update_task(
        self,
        state: TaskState,
        *,
        assistant_message: str | None = None,
        response_payload: dict | None = None,
    ) -> None:
        if not state.conversation_id or not state.turn_id:
            return
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE task_index SET outcome = ?, run_id = ?, updated_at = ?
                WHERE tenant_id = ? AND task_id = ?""",
                (state.outcome.value, state.run_id, now, state.principal.tenant_id, state.task_id),
            )
            self._mirror_task_state(connection, state, now)
            if response_payload is not None:
                connection.execute(
                    """UPDATE turns SET response_payload=?
                    WHERE tenant_id=? AND conversation_id=? AND turn_id=?""",
                    (
                        json.dumps(response_payload, ensure_ascii=False),
                        state.principal.tenant_id,
                        state.conversation_id,
                        state.turn_id,
                    ),
                )
            if assistant_message:
                exists = connection.execute(
                    """SELECT 1 FROM messages WHERE tenant_id = ? AND turn_id = ?
                    AND role = 'assistant' AND content = ?""",
                    (state.principal.tenant_id, state.turn_id, assistant_message),
                ).fetchone()
                if exists is None:
                    connection.execute(
                        """INSERT INTO messages(
                            message_id, conversation_id, turn_id, tenant_id, role, content,
                            intent, task_id, product_refs, created_at
                        ) VALUES(?, ?, ?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                        (
                            f"msg_{uuid4().hex[:12]}", state.conversation_id,
                            state.turn_id, state.principal.tenant_id, assistant_message,
                            state.intent, state.task_id,
                            json.dumps(state.entity_refs, ensure_ascii=False), now,
                        ),
                    )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE tenant_id = ? AND conversation_id = ?",
                (now, state.principal.tenant_id, state.conversation_id),
            )
            connection.commit()

    def _mirror_task_state(
        self,
        connection: sqlite3.Connection,
        state: TaskState,
        now: str,
        *,
        preferred_task_session_id: str | None = None,
    ) -> tuple[str, str]:
        """Atomically project a legacy TaskState into the v40 identity model."""

        if not state.conversation_id or not state.turn_id:
            raise ConversationStoreError("Task is missing conversation metadata")
        tenant_id = state.principal.tenant_id
        runtime_run_id = state.run_id or f"run_{state.task_id}"
        existing = connection.execute(
            """SELECT task_session_id FROM task_sessions
            WHERE tenant_id=? AND current_task_id=?""",
            (tenant_id, state.task_id),
        ).fetchone()
        precreated = None
        if existing is None and preferred_task_session_id is not None:
            precreated = connection.execute(
                """SELECT task_session_id FROM task_sessions
                WHERE tenant_id=? AND task_session_id=? AND conversation_id=?""",
                (tenant_id, preferred_task_session_id, state.conversation_id),
            ).fetchone()
            if precreated is None:
                raise ConversationNotFoundError("Preferred task session not found")
        if existing is None and precreated is None:
            precreated = connection.execute(
                """SELECT ts.task_session_id FROM task_sessions ts
                JOIN turn_task_links ttl
                  ON ttl.tenant_id=ts.tenant_id
                 AND ttl.task_session_id=ts.task_session_id
                WHERE ts.tenant_id=? AND ttl.turn_id=?
                  AND ts.current_task_id IS NULL
                ORDER BY ttl.linked_at DESC LIMIT 1""",
                (tenant_id, state.turn_id),
            ).fetchone()
        task_session_id = (
            existing["task_session_id"]
            if existing is not None
            else precreated["task_session_id"]
            if precreated is not None
            else f"session_{uuid4().hex[:12]}"
        )
        session_already_exists = existing is not None or precreated is not None
        status = _task_session_status(state.outcome.value)
        completed_at = now if status in _TERMINAL_TASK_SESSION_STATUSES else None
        connection.execute(
            """INSERT INTO task_sessions(
                task_session_id, tenant_id, conversation_id, origin_turn_id,
                intent, title, status, checkpoint_thread_id, current_task_id,
                entity_refs, state_version, created_at, updated_at, completed_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_session_id) DO UPDATE SET
                intent=excluded.intent,
                status=excluded.status,
                current_task_id=excluded.current_task_id,
                entity_refs=excluded.entity_refs,
                state_version=excluded.state_version,
                updated_at=excluded.updated_at,
                completed_at=excluded.completed_at""",
            (
                task_session_id,
                tenant_id,
                state.conversation_id,
                state.turn_id,
                state.intent or "create_listing",
                _title(state.goal),
                status,
                task_session_id,
                state.task_id,
                json.dumps(state.entity_refs, ensure_ascii=False),
                max(0, state.state_version),
                now,
                now,
                completed_at,
            ),
        )
        self._link_turn_task(
            connection,
            tenant_id=tenant_id,
            turn_id=state.turn_id,
            task_session_id=task_session_id,
            relation="continued" if existing is not None else "created",
            linked_at=now,
        )

        run_row = connection.execute(
            "SELECT workflow_run_id FROM workflow_runs WHERE tenant_id=? AND run_id=?",
            (tenant_id, runtime_run_id),
        ).fetchone()
        workflow_run_id = (
            run_row["workflow_run_id"]
            if run_row is not None
            else f"workflow_{uuid4().hex[:12]}"
        )
        parent_workflow_run_id = None
        if state.parent_run_id:
            parent = connection.execute(
                """SELECT workflow_run_id FROM workflow_runs
                WHERE tenant_id=? AND run_id=?""",
                (tenant_id, state.parent_run_id),
            ).fetchone()
            parent_workflow_run_id = parent["workflow_run_id"] if parent else None
        workflow_status = _workflow_run_status(state.outcome.value)
        workflow_completed_at = (
            now if workflow_status in _TERMINAL_WORKFLOW_RUN_STATUSES else None
        )
        connection.execute(
            """INSERT INTO workflow_runs(
                workflow_run_id, task_session_id, tenant_id, conversation_id,
                trigger_turn_id, task_id, run_id, parent_workflow_run_id, status,
                checkpoint_thread_id, checkpoint_namespace, attempt, execution_epoch,
                started_at, updated_at, completed_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_run_id) DO UPDATE SET
                status=excluded.status,
                parent_workflow_run_id=COALESCE(
                    excluded.parent_workflow_run_id, workflow_runs.parent_workflow_run_id
                ),
                attempt=excluded.attempt,
                execution_epoch=excluded.execution_epoch,
                updated_at=excluded.updated_at,
                completed_at=excluded.completed_at""",
            (
                workflow_run_id,
                task_session_id,
                tenant_id,
                state.conversation_id,
                state.turn_id,
                state.task_id,
                runtime_run_id,
                parent_workflow_run_id,
                workflow_status,
                task_session_id,
                runtime_run_id,
                max(1, state.resume_count + 1),
                max(1, state.checkpoint_version),
                state.created_at.isoformat(),
                now,
                workflow_completed_at,
            ),
        )
        connection.execute(
            """UPDATE task_sessions SET active_workflow_run_id=?, updated_at=?
            WHERE tenant_id=? AND task_session_id=?""",
            (workflow_run_id, now, tenant_id, task_session_id),
        )
        return task_session_id, workflow_run_id

    @staticmethod
    def _link_turn_task(
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        turn_id: str,
        task_session_id: str,
        relation: str,
        linked_at: str,
    ) -> None:
        allowed = {"created", "continued", "recalled", "switched", "batch_item"}
        if relation not in allowed:
            raise ConversationStoreError("Unsupported turn-to-task relation")
        connection.execute(
            """INSERT OR IGNORE INTO turn_task_links(
                tenant_id, turn_id, task_session_id, relation, linked_at
            ) VALUES(?, ?, ?, ?, ?)""",
            (tenant_id, turn_id, task_session_id, relation, linked_at),
        )

    def fail_turn(self, tenant_id: str, turn_id: str, error_code: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE turns SET status = 'failed', error_code = ?, completed_at = ? WHERE tenant_id = ? AND turn_id = ?",
                (error_code[:120], _now(), tenant_id, turn_id),
            )

    def task_for_request(self, tenant_id: str, client_request_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id FROM turns WHERE tenant_id = ? AND client_request_id = ?",
                (tenant_id, client_request_id),
            ).fetchone()
        return row["task_id"] if row and row["task_id"] else None

    def latest_task_id(self, tenant_id: str, conversation_id: str) -> str | None:
        self.get_conversation(tenant_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id FROM task_index WHERE tenant_id = ? AND conversation_id = ? ORDER BY updated_at DESC LIMIT 1",
                (tenant_id, conversation_id),
            ).fetchone()
        return row["task_id"] if row else None

    def latest_response_payload(
        self, tenant_id: str, conversation_id: str
    ) -> dict | None:
        self.get_conversation(tenant_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT response_payload FROM turns
                WHERE tenant_id=? AND conversation_id=? AND response_payload IS NOT NULL
                ORDER BY ordinal DESC LIMIT 1""",
                (tenant_id, conversation_id),
            ).fetchone()
        return json.loads(row["response_payload"]) if row else None

    def backfill_task(self, state: TaskState) -> tuple[str, str]:
        tenant_id = state.principal.tenant_id
        if state.conversation_id and state.turn_id:
            return state.conversation_id, state.turn_id
        conversation = self.create_conversation(tenant_id, title=state.goal)
        reservation = self.begin_turn(
            tenant_id,
            conversation.conversation_id,
            client_request_id=f"legacy_{state.task_id}",
            message=state.goal,
            intent=state.intent or "create_listing",
        )
        state.conversation_id = conversation.conversation_id
        state.turn_id = reservation.turn.turn_id
        self.complete_turn(state, "这是从旧版 Checkpoint 回填的历史任务。")
        return state.conversation_id, state.turn_id

    def _get_turn(self, tenant_id: str, turn_id: str) -> TurnRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE tenant_id = ? AND turn_id = ?",
                (tenant_id, turn_id),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError("Turn not found")
        return _turn(row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title(content: str) -> str:
    compact = " ".join(content.strip().split())
    return compact[:36] or "新会话"


def _validate_id(value: str, field_name: str) -> None:
    if not SAFE_ID.fullmatch(value):
        raise ConversationStoreError(f"Invalid {field_name}")


def _conversation(row: sqlite3.Row) -> ConversationRecord:
    return ConversationRecord(**{key: row[key] for key in ConversationRecord.model_fields})


def _turn(row: sqlite3.Row) -> TurnRecord:
    payload = {
        key: row[key]
        for key in TurnRecord.model_fields
        if key != "response_payload"
    }
    payload["response_payload"] = (
        json.loads(row["response_payload"]) if row["response_payload"] else None
    )
    return TurnRecord(**payload)


def _message(row: sqlite3.Row) -> MessageRecord:
    payload = {key: row[key] for key in MessageRecord.model_fields if key != "product_refs"}
    payload["product_refs"] = json.loads(row["product_refs"] or "[]")
    return MessageRecord(**payload)


def _task(row: sqlite3.Row) -> TaskIndexRecord:
    return TaskIndexRecord(**{key: row[key] for key in TaskIndexRecord.model_fields})


def _task_session(row: sqlite3.Row) -> TaskSessionRecord:
    payload = {
        key: row[key]
        for key in TaskSessionRecord.model_fields
        if key != "entity_refs"
    }
    payload["entity_refs"] = json.loads(row["entity_refs"] or "[]")
    return TaskSessionRecord(**payload)


def _workflow_run(row: sqlite3.Row) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        **{key: row[key] for key in WorkflowRunRecord.model_fields}
    )


def _batch_job(row: sqlite3.Row) -> BatchJobRecord:
    return BatchJobRecord(**{key: row[key] for key in BatchJobRecord.model_fields})


def _batch_item(row: sqlite3.Row) -> BatchItemRecord:
    payload = {
        key: row[key]
        for key in BatchItemRecord.model_fields
        if key not in {"request_payload", "execution_history"}
    }
    payload["request_payload"] = json.loads(row["request_payload"] or "{}")
    payload["execution_history"] = json.loads(row["execution_history"] or "[]")
    return BatchItemRecord(**payload)


def _task_session_status(outcome: str) -> str:
    if outcome in {
        "created",
        "running",
        "waiting_for_input",
        "awaiting_approval",
        "completed",
        "business_rejected",
        "technical_failed",
        "needs_attention",
    }:
        return outcome
    return "technical_failed"


def _workflow_run_status(outcome: str) -> str:
    return _task_session_status(outcome)
