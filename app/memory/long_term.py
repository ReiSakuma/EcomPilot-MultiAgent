from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.config import CONVERSATION_DATABASE_PATH


MemoryStatus = Literal["candidate", "active", "inactive", "conflicted"]
Sensitivity = Literal["public", "internal", "restricted"]
SENSITIVITY_LEVEL = {"public": 0, "internal": 1, "restricted": 2}


class MerchantMemory(BaseModel):
    """A tenant-scoped preference or rule; candidates are never recalled."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(default_factory=lambda: f"mem_{uuid4().hex[:12]}")
    tenant_id: str = "tenant_demo"
    scope: str
    memory_type: str
    content: str
    source: str
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: MemoryStatus = "active"
    sensitivity: Sensitivity = "internal"
    conflict_key: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LongTermMemory:
    """SQLite-backed merchant memory with confirmation and tenant isolation."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or CONVERSATION_DATABASE_PATH
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
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
                CREATE INDEX IF NOT EXISTS idx_merchant_memory_conflict
                    ON merchant_memories(tenant_id, conflict_key, status);
                """
            )

    def add(self, memory: MerchantMemory) -> MerchantMemory:
        memory.updated_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO merchant_memories(
                    memory_id, tenant_id, scope, memory_type, content, source,
                    confidence, status, sensitivity, conflict_key, valid_from,
                    valid_until, confirmed_by, confirmed_at, metadata, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    content=excluded.content, confidence=excluded.confidence,
                    status=excluded.status, sensitivity=excluded.sensitivity,
                    valid_from=excluded.valid_from, valid_until=excluded.valid_until,
                    confirmed_by=excluded.confirmed_by, confirmed_at=excluded.confirmed_at,
                    metadata=excluded.metadata, updated_at=excluded.updated_at""",
                _memory_values(memory),
            )
        return self.get(memory.tenant_id, memory.memory_id)

    def propose(
        self,
        tenant_id: str,
        *,
        scope: str,
        memory_type: str,
        content: str,
        source: str = "user_message",
        confidence: float = 1.0,
        sensitivity: Sensitivity = "internal",
        conflict_key: str | None = None,
        valid_until: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MerchantMemory:
        if not content.strip():
            raise ValueError("Memory content must not be blank")
        return self.add(
            MerchantMemory(
                tenant_id=tenant_id,
                scope=scope.strip() or "global",
                memory_type=memory_type,
                content=content.strip(),
                source=source,
                confidence=confidence,
                status="candidate",
                sensitivity=sensitivity,
                conflict_key=conflict_key,
                valid_until=valid_until,
                metadata=metadata or {},
            )
        )

    def confirm(self, tenant_id: str, memory_id: str, *, confirmed_by: str) -> MerchantMemory:
        candidate = self.get(tenant_id, memory_id)
        if candidate.status not in {"candidate", "active"}:
            raise ValueError("Only candidate or active memory can be confirmed")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conflict = None
            if candidate.conflict_key:
                conflict = connection.execute(
                    """SELECT memory_id FROM merchant_memories
                    WHERE tenant_id=? AND conflict_key=? AND status='active'
                    AND memory_id<>? AND content<>? LIMIT 1""",
                    (tenant_id, candidate.conflict_key, memory_id, candidate.content),
                ).fetchone()
            if conflict:
                connection.execute(
                    """UPDATE merchant_memories SET status='conflicted', updated_at=?
                    WHERE tenant_id=? AND conflict_key=? AND status='active'""",
                    (now, tenant_id, candidate.conflict_key),
                )
                status = "conflicted"
            else:
                status = "active"
            changed = connection.execute(
                """UPDATE merchant_memories SET status=?, confirmed_by=?, confirmed_at=?,
                valid_from=COALESCE(valid_from, ?), updated_at=?
                WHERE tenant_id=? AND memory_id=?""",
                (status, confirmed_by, now, now, now, tenant_id, memory_id),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise KeyError("Memory not found")
            connection.commit()
        return self.get(tenant_id, memory_id)

    def deactivate(self, tenant_id: str, memory_id: str) -> MerchantMemory:
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE merchant_memories SET status='inactive', updated_at=?
                WHERE tenant_id=? AND memory_id=?""",
                (datetime.now(timezone.utc).isoformat(), tenant_id, memory_id),
            ).rowcount
        if changed != 1:
            raise KeyError("Memory not found")
        return self.get(tenant_id, memory_id)

    def get(self, tenant_id: str, memory_id: str) -> MerchantMemory:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM merchant_memories WHERE tenant_id=? AND memory_id=?",
                (tenant_id, memory_id),
            ).fetchone()
        if row is None:
            raise KeyError("Memory not found")
        return _memory_from_row(row)

    def list(self, tenant_id: str, *, status: MemoryStatus | None = None) -> list[MerchantMemory]:
        query = "SELECT * FROM merchant_memories WHERE tenant_id=?"
        args: list[Any] = [tenant_id]
        if status:
            query += " AND status=?"
            args.append(status)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return [_memory_from_row(row) for row in rows]

    def search(
        self,
        scope: str,
        *,
        tenant_id: str = "tenant_demo",
        query: str | None = None,
        max_sensitivity: Sensitivity = "internal",
        limit: int = 5,
        now: datetime | None = None,
    ) -> list[MerchantMemory]:
        current = now or datetime.now(timezone.utc)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM merchant_memories
                WHERE tenant_id=? AND status='active' AND (scope=? OR scope='global')
                AND (valid_from IS NULL OR valid_from<=?)
                AND (valid_until IS NULL OR valid_until>?)""",
                (tenant_id, scope, current.isoformat(), current.isoformat()),
            ).fetchall()
        allowed = [
            _memory_from_row(row)
            for row in rows
            if SENSITIVITY_LEVEL[row["sensitivity"]] <= SENSITIVITY_LEVEL[max_sensitivity]
        ]
        return sorted(
            allowed,
            key=lambda item: (_bm25_score(query or scope, item.content), item.confidence),
            reverse=True,
        )[: max(1, min(limit, 20))]

    def snippets(
        self,
        scope: str,
        limit: int = 5,
        *,
        tenant_id: str = "tenant_demo",
        query: str | None = None,
        max_sensitivity: Sensitivity = "internal",
    ) -> list[str]:
        return [
            f"{item.memory_id}: {item.content}"
            for item in self.search(
                scope,
                tenant_id=tenant_id,
                query=query,
                max_sensitivity=max_sensitivity,
                limit=limit,
            )
        ]


def seed_default_merchant_memory(database_path: Path | None = None) -> LongTermMemory:
    memory = LongTermMemory(database_path)
    defaults = (
        ("mem_system_brand_rule", "global", "brand_rule", "品牌表达偏年轻、清晰、务实，避免夸张和绝对化营销词。", 0.85),
        ("mem_system_earphone_experience", "无线耳机", "category_experience", "无线耳机学生人群更关注低延迟、佩戴舒适、宿舍通话和续航稳定性。", 0.82),
        ("mem_system_margin_rule", "global", "merchant_constraint", "默认不建议牺牲 25% 以下毛利率换取冷启动销量。", 0.9),
    )
    now = datetime.now(timezone.utc)
    for memory_id, scope, memory_type, content, confidence in defaults:
        memory.add(MerchantMemory(
            memory_id=memory_id, tenant_id="tenant_demo", scope=scope,
            memory_type=memory_type, content=content, source="system_rule_v33",
            confidence=confidence, status="active", confirmed_by="system_policy",
            confirmed_at=now,
        ))
    return memory


def _memory_values(memory: MerchantMemory) -> tuple[Any, ...]:
    return (
        memory.memory_id, memory.tenant_id, memory.scope, memory.memory_type,
        memory.content, memory.source, memory.confidence, memory.status,
        memory.sensitivity, memory.conflict_key,
        memory.valid_from.isoformat() if memory.valid_from else None,
        memory.valid_until.isoformat() if memory.valid_until else None,
        memory.confirmed_by,
        memory.confirmed_at.isoformat() if memory.confirmed_at else None,
        json.dumps(memory.metadata, ensure_ascii=False), memory.created_at.isoformat(),
        memory.updated_at.isoformat(),
    )


def _memory_from_row(row: sqlite3.Row) -> MerchantMemory:
    return MerchantMemory(
        memory_id=row["memory_id"], tenant_id=row["tenant_id"], scope=row["scope"],
        memory_type=row["memory_type"], content=row["content"], source=row["source"],
        confidence=row["confidence"], status=row["status"], sensitivity=row["sensitivity"],
        conflict_key=row["conflict_key"], valid_from=row["valid_from"],
        valid_until=row["valid_until"], confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"], metadata=json.loads(row["metadata"] or "{}"),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    latin = re.findall(r"[a-z0-9_]+", normalized)
    chinese = [normalized[index:index + 2] for index in range(max(0, len(normalized) - 1))]
    return latin + chinese


def _bm25_score(query: str, document: str) -> float:
    query_terms = set(_tokens(query))
    document_terms = _tokens(document)
    if not query_terms or not document_terms:
        return 0.0
    frequencies = {term: document_terms.count(term) for term in query_terms}
    normalizer = 1.0 + 0.75 * max(0, len(document_terms) - 20) / 20
    return sum(math.log1p(count) for count in frequencies.values()) / normalizer
