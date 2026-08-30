from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.conversations.repository import ConversationNotFoundError, ConversationRepository
from app.context.budget import ContextBudgetManager
from app.context.schemas import BudgetedContextItem
from app.orchestration.state import TaskState
from app.orchestration.planner import extract_constraints


class StructuredConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    conversation_id: str
    protocol_version: Literal["1.0", "2.0"] = "2.0"
    trust_status: Literal["active", "stale", "rejected"] = "active"
    summary_version: int = Field(default=1, ge=1)
    goals: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_items: list[str] = Field(default_factory=list)
    product_refs: list[str] = Field(default_factory=list)
    task_refs: list[str] = Field(default_factory=list)
    source_turn_count: int = Field(default=0, ge=0)
    source_message_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    source_versions: dict[str, str] = Field(default_factory=dict)
    fact_snapshot: dict[str, Any] = Field(default_factory=dict)
    generator: str = "deterministic-summary-v2"
    content_hash: str = ""
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EntityMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_memory_id: str = Field(default_factory=lambda: f"entitymem_{uuid4().hex[:12]}")
    tenant_id: str
    conversation_id: str
    entity_type: Literal["product", "task", "artifact"]
    entity_id: str
    relation: Literal["active", "recent", "referenced"] = "referenced"
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationMemoryService:
    """Builds restart-safe turn, summary, and entity memory projections."""

    def __init__(self, repository: ConversationRepository) -> None:
        self.repository = repository
        self.database_path = repository.database_path
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
                    PRIMARY KEY(tenant_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS context_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary_version INTEGER,
                    details TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_context_events_conversation
                    ON context_events(tenant_id, conversation_id, event_id);
                CREATE TABLE IF NOT EXISTS entity_memories (
                    entity_memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL CHECK(entity_type IN ('product','task','artifact')),
                    entity_id TEXT NOT NULL,
                    relation TEXT NOT NULL CHECK(relation IN ('active','recent','referenced')),
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, conversation_id, entity_type, entity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_entity_memory_context
                    ON entity_memories(tenant_id, conversation_id, updated_at DESC);
                """
            )
            columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(conversation_summaries)"
                ).fetchall()
            }
            additions = {
                "protocol_version": "TEXT NOT NULL DEFAULT '2.0'",
                "trust_status": "TEXT NOT NULL DEFAULT 'active'",
                "source_message_ids": "TEXT NOT NULL DEFAULT '[]'",
                "artifact_refs": "TEXT NOT NULL DEFAULT '[]'",
                "source_versions": "TEXT NOT NULL DEFAULT '{}'",
                "fact_snapshot": "TEXT NOT NULL DEFAULT '{}'",
                "generator": "TEXT NOT NULL DEFAULT 'deterministic-summary-v2'",
                "content_hash": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE conversation_summaries ADD COLUMN {name} {declaration}"
                    )

    def refresh_summary(
        self, tenant_id: str, conversation_id: str
    ) -> StructuredConversationSummary:
        detail = self.repository.get_detail(tenant_id, conversation_id)
        previous = self.get_summary(tenant_id, conversation_id)
        older_messages = detail.messages[:-6] if len(detail.messages) > 6 else []
        goals = _unique([
            _first_sentences(message.content)
            for message in older_messages
            if message.role == "user"
        ])[-6:]
        decisions = _unique([
            _first_sentences(message.content)
            for message in older_messages
            if message.role == "assistant"
        ])[-6:]
        open_items = [
            turn.error_code or "等待补充信息"
            for turn in detail.turns
            if turn.status in {"processing", "failed"}
        ][-4:]
        product_refs = _unique([
            ref for message in detail.messages for ref in message.product_refs
        ])[-12:]
        task_refs = _unique([task.task_id for task in detail.tasks])[-12:]
        source_messages = older_messages or detail.messages
        source_ids = [message.message_id for message in source_messages]
        source_versions = {
            message.message_id: _message_version(message.content, message.created_at.isoformat())
            for message in source_messages
        }
        fact_snapshot: dict[str, Any] = {}
        for message in detail.messages:
            if message.role == "user":
                fact_snapshot.update(extract_constraints(message.content))
        artifact_refs = _unique([
            ref for memory in self.list_entity_memories(tenant_id, conversation_id)
            if memory["entity_type"] == "artifact"
            for ref in [memory["entity_id"]]
        ])[-20:]
        summary = StructuredConversationSummary(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            summary_version=(previous.summary_version + 1 if previous else 1),
            goals=goals,
            decisions=decisions,
            open_items=open_items,
            product_refs=product_refs,
            task_refs=task_refs,
            source_turn_count=len(detail.turns),
            source_message_ids=source_ids,
            artifact_refs=artifact_refs,
            source_versions=source_versions,
            fact_snapshot=fact_snapshot,
        )
        summary.content_hash = _summary_hash(summary)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO conversation_summaries(
                    tenant_id, conversation_id, summary_version, goals, decisions,
                    open_items, product_refs, task_refs, source_turn_count, updated_at,
                    protocol_version, trust_status, source_message_ids, artifact_refs,
                    source_versions, fact_snapshot, generator, content_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id, conversation_id) DO UPDATE SET
                    summary_version=excluded.summary_version, goals=excluded.goals,
                    decisions=excluded.decisions, open_items=excluded.open_items,
                    product_refs=excluded.product_refs, task_refs=excluded.task_refs,
                    source_turn_count=excluded.source_turn_count, updated_at=excluded.updated_at,
                    protocol_version=excluded.protocol_version, trust_status=excluded.trust_status,
                    source_message_ids=excluded.source_message_ids,
                    artifact_refs=excluded.artifact_refs, source_versions=excluded.source_versions,
                    fact_snapshot=excluded.fact_snapshot, generator=excluded.generator,
                    content_hash=excluded.content_hash""",
                (
                    tenant_id, conversation_id, summary.summary_version,
                    json.dumps(summary.goals, ensure_ascii=False),
                    json.dumps(summary.decisions, ensure_ascii=False),
                    json.dumps(summary.open_items, ensure_ascii=False),
                    json.dumps(summary.product_refs, ensure_ascii=False),
                    json.dumps(summary.task_refs, ensure_ascii=False),
                    summary.source_turn_count, summary.updated_at.isoformat(),
                    summary.protocol_version, summary.trust_status,
                    json.dumps(summary.source_message_ids),
                    json.dumps(summary.artifact_refs),
                    json.dumps(summary.source_versions, ensure_ascii=False),
                    json.dumps(summary.fact_snapshot, ensure_ascii=False),
                    summary.generator, summary.content_hash,
                ),
            )
            self._record_context_event(
                connection, tenant_id, conversation_id, "summary_refreshed",
                summary.summary_version, {"source_count": len(source_ids), "hash": summary.content_hash},
            )
        return summary

    def get_summary(
        self, tenant_id: str, conversation_id: str
    ) -> StructuredConversationSummary | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM conversation_summaries
                WHERE tenant_id=? AND conversation_id=?""",
                (tenant_id, conversation_id),
            ).fetchone()
        if row is None:
            return None
        return StructuredConversationSummary(
            tenant_id=row["tenant_id"], conversation_id=row["conversation_id"],
            summary_version=row["summary_version"], goals=json.loads(row["goals"]),
            decisions=json.loads(row["decisions"]), open_items=json.loads(row["open_items"]),
            product_refs=json.loads(row["product_refs"]), task_refs=json.loads(row["task_refs"]),
            source_turn_count=row["source_turn_count"], updated_at=row["updated_at"],
            protocol_version=row["protocol_version"], trust_status=row["trust_status"],
            source_message_ids=json.loads(row["source_message_ids"]),
            artifact_refs=json.loads(row["artifact_refs"]),
            source_versions=json.loads(row["source_versions"]),
            fact_snapshot=json.loads(row["fact_snapshot"]), generator=row["generator"],
            content_hash=row["content_hash"],
        )

    def validate_summary(
        self, tenant_id: str, conversation_id: str
    ) -> tuple[bool, list[str]]:
        summary = self.get_summary(tenant_id, conversation_id)
        if summary is None:
            return False, ["summary_missing"]
        detail = self.repository.get_detail(tenant_id, conversation_id)
        by_id = {message.message_id: message for message in detail.messages}
        issues: list[str] = []
        for message_id, version in summary.source_versions.items():
            message = by_id.get(message_id)
            if message is None or _message_version(message.content, message.created_at.isoformat()) != version:
                issues.append(f"source_changed:{message_id}")
        authoritative: dict[str, Any] = {}
        for message in detail.messages:
            if message.role == "user":
                authoritative.update(extract_constraints(message.content))
        for key, value in summary.fact_snapshot.items():
            if key in authoritative and authoritative[key] != value:
                issues.append(f"fact_conflict:{key}")
        expected_hash = _summary_hash(summary.model_copy(update={"content_hash": ""}))
        if summary.content_hash != expected_hash:
            issues.append("content_hash_mismatch")
        if issues:
            self._set_summary_status(tenant_id, conversation_id, "rejected", issues)
            return False, issues
        return summary.trust_status == "active", []

    def replay_summary(
        self, tenant_id: str, conversation_id: str
    ) -> dict[str, Any]:
        """Replay immutable message versions and report whether the derived cache still matches."""
        valid, issues = self.validate_summary(tenant_id, conversation_id)
        summary = self.get_summary(tenant_id, conversation_id)
        with self._connect() as connection:
            self._record_context_event(
                connection, tenant_id, conversation_id, "summary_replayed",
                summary.summary_version if summary else None,
                {"valid": valid, "issues": issues},
            )
        return {
            "valid": valid,
            "issues": issues,
            "summary_version": summary.summary_version if summary else None,
            "source_message_ids": summary.source_message_ids if summary else [],
        }

    def list_context_events(
        self, tenant_id: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM context_events WHERE tenant_id=? AND conversation_id=?
                ORDER BY event_id""",
                (tenant_id, conversation_id),
            ).fetchall()
        return [{
            "event_id": row["event_id"], "event_type": row["event_type"],
            "summary_version": row["summary_version"],
            "details": json.loads(row["details"]), "created_at": row["created_at"],
        } for row in rows]

    def list_entity_memories(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM entity_memories WHERE tenant_id=? AND conversation_id=?
                ORDER BY updated_at DESC LIMIT 20""",
                (tenant_id, conversation_id),
            ).fetchall()
        return [{
            "entity_type": row["entity_type"], "entity_id": row["entity_id"],
            "relation": row["relation"], "metadata": json.loads(row["metadata"]),
        } for row in rows]

    def capture_task_entities(self, state: TaskState) -> None:
        if not state.conversation_id:
            return
        entries: list[EntityMemory] = []
        entries.extend(EntityMemory(
            tenant_id=state.principal.tenant_id, conversation_id=state.conversation_id,
            entity_type="product", entity_id=entity_id, relation="active",
            metadata={"task_id": state.task_id},
        ) for entity_id in state.entity_refs)
        entries.append(EntityMemory(
            tenant_id=state.principal.tenant_id, conversation_id=state.conversation_id,
            entity_type="task", entity_id=state.task_id, relation="recent",
            metadata={"intent": state.intent, "outcome": state.outcome.value},
        ))
        entries.extend(EntityMemory(
            tenant_id=state.principal.tenant_id, conversation_id=state.conversation_id,
            entity_type="artifact", entity_id=artifact_id, relation="referenced",
            metadata={"artifact_type": artifact.artifact_type},
        ) for artifact_id, artifact in state.artifacts.items())
        with self._connect() as connection:
            for entry in entries:
                connection.execute(
                    """INSERT INTO entity_memories(
                        entity_memory_id, tenant_id, conversation_id, entity_type,
                        entity_id, relation, metadata, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(tenant_id, conversation_id, entity_type, entity_id)
                    DO UPDATE SET relation=excluded.relation, metadata=excluded.metadata,
                    updated_at=excluded.updated_at""",
                    (
                        entry.entity_memory_id, entry.tenant_id, entry.conversation_id,
                        entry.entity_type, entry.entity_id, entry.relation,
                        json.dumps(entry.metadata, ensure_ascii=False), entry.updated_at.isoformat(),
                    ),
                )

    def context_seed(
        self, tenant_id: str, conversation_id: str, *, next_input: str = ""
    ) -> dict[str, Any]:
        try:
            detail = self.repository.get_detail(tenant_id, conversation_id)
        except ConversationNotFoundError:
            # Compatibility adapters may invoke the graph without first reserving a turn.
            return {"conversation_summary": {}, "recent_turns": [], "entity_memory": []}
        summary = self.get_summary(tenant_id, conversation_id)
        summary_valid, summary_issues = self.validate_summary(tenant_id, conversation_id) if summary else (False, [])
        entity_memory = self.list_entity_memories(tenant_id, conversation_id)
        recent_turns = [
            {
                "role": message.role,
                "intent": message.intent,
                "task_id": message.task_id,
                "product_refs": message.product_refs,
                "content": _first_sentences(message.content),
            }
            for message in detail.messages[-6:]
        ]
        budget = ContextBudgetManager().decide([
            BudgetedContextItem(
                item_id="security_and_authority", priority="P0",
                content={"tenant_id": tenant_id, "write_from_summary_forbidden": True},
            ),
            BudgetedContextItem(
                item_id="current_entities", priority="P1", content=entity_memory,
                source_refs=[item["entity_id"] for item in entity_memory],
            ),
            BudgetedContextItem(
                item_id="recent_turns", priority="P2", content=recent_turns,
                source_refs=[message.message_id for message in detail.messages[-6:]],
            ),
            BudgetedContextItem(
                item_id="conversation_summary", priority="P3",
                content=summary.model_dump(mode="json") if summary_valid and summary else {},
                source_refs=summary.source_message_ids if summary_valid and summary else [],
            ),
            BudgetedContextItem(
                item_id="debug_history", priority="P4", content=[],
            ),
        ], next_input=next_input)
        return {
            "conversation_summary": (
                summary.model_dump(mode="json") if summary_valid and summary else {}
            ),
            "summary_trust": {
                "valid": summary_valid,
                "issues": summary_issues,
                "write_authority": False,
            },
            "context_budget": budget.model_dump(mode="json"),
            "recent_turns": recent_turns,
            "entity_memory": entity_memory,
        }

    def _set_summary_status(
        self, tenant_id: str, conversation_id: str,
        status: Literal["stale", "rejected"], issues: list[str],
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT summary_version FROM conversation_summaries WHERE tenant_id=? AND conversation_id=?",
                (tenant_id, conversation_id),
            ).fetchone()
            connection.execute(
                "UPDATE conversation_summaries SET trust_status=? WHERE tenant_id=? AND conversation_id=?",
                (status, tenant_id, conversation_id),
            )
            self._record_context_event(
                connection, tenant_id, conversation_id, "summary_rejected",
                row["summary_version"] if row else None, {"issues": issues},
            )

    @staticmethod
    def _record_context_event(
        connection: sqlite3.Connection, tenant_id: str, conversation_id: str,
        event_type: str, summary_version: int | None, details: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO context_events(
                tenant_id, conversation_id, event_type, summary_version, details, created_at
            ) VALUES(?,?,?,?,?,?)""",
            (tenant_id, conversation_id, event_type, summary_version,
             json.dumps(details, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )


def _first_sentences(text: str, limit: int = 2) -> str:
    parts = [part.strip() for part in text.replace("！", "。 ").replace("？", "。 ").split("。")]
    selected = [part for part in parts if part][:limit]
    return "。".join(selected) + ("。" if selected else "")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _message_version(content: str, created_at: str) -> str:
    return hashlib.sha256(f"{created_at}\n{content}".encode("utf-8")).hexdigest()


def _summary_hash(summary: StructuredConversationSummary) -> str:
    payload = summary.model_dump(mode="json", exclude={"content_hash", "updated_at"})
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
