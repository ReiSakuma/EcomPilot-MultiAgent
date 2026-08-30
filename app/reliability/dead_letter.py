from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from app.config import DATA_DIR
from app.reliability.models import DeadLetterRecord


class DeadLetterStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (DATA_DIR / "reliability_v36.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letters (
                    record_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    error_signature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, error_signature, status)
                )
                """
            )

    def enqueue(self, record: DeadLetterRecord) -> DeadLetterRecord:
        payload = record.model_dump_json()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM dead_letters WHERE task_id=? AND error_signature=? AND status='needs_attention'",
                (record.task_id, record.error_signature),
            ).fetchone()
            if existing:
                return DeadLetterRecord.model_validate_json(existing["payload_json"])
            connection.execute(
                "INSERT INTO dead_letters VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.record_id,
                    record.task_id,
                    record.tenant_id,
                    record.error_signature,
                    record.status,
                    payload,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record
    def list(self, *, tenant_id: str, task_id: str | None = None) -> list[DeadLetterRecord]:
        query = "SELECT payload_json FROM dead_letters WHERE tenant_id=?"
        params: list[str] = [tenant_id]
        if task_id:
            query += " AND task_id=?"
            params.append(task_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DeadLetterRecord.model_validate_json(row["payload_json"]) for row in rows]

    def resolve(self, record_id: str, *, tenant_id: str) -> DeadLetterRecord:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM dead_letters WHERE record_id=? AND tenant_id=?",
                (record_id, tenant_id),
            ).fetchone()
            if row is None:
                raise KeyError(record_id)
            record = DeadLetterRecord.model_validate_json(row["payload_json"])
            record.status = "resolved"
            record.updated_at = datetime.now(timezone.utc)
            connection.execute(
                "UPDATE dead_letters SET status=?, payload_json=?, updated_at=? WHERE record_id=?",
                (record.status, record.model_dump_json(), record.updated_at.isoformat(), record_id),
            )
        return record


_DEFAULT_STORE: DeadLetterStore | None = None


def get_dead_letter_store() -> DeadLetterStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = DeadLetterStore()
    return _DEFAULT_STORE
