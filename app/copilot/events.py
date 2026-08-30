from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.config import CONVERSATION_DATABASE_PATH
from app.copilot.schemas import CopilotEvent, CopilotResponse


class CopilotEventStore:
    """Tenant-scoped durable event stream used by the product UI and reconnects."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or CONVERSATION_DATABASE_PATH

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create(self, tenant_id: str, conversation_id: str) -> str:
        stream_id = f"stream_{uuid4().hex[:12]}"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO copilot_streams(
                    stream_id, tenant_id, conversation_id, status, created_at, updated_at
                ) VALUES(?, ?, ?, 'running', ?, ?)""",
                (stream_id, tenant_id, conversation_id, now, now),
            )
        self.append(
            tenant_id, stream_id,
            event_type="request_received", stage="orchestrator", status="running",
            title="已接收任务", detail="正在理解你的需求并选择所需能力。",
        )
        return stream_id

    def append(
        self,
        tenant_id: str,
        stream_id: str,
        *,
        event_type: str,
        stage: str,
        status: str,
        title: str,
        detail: str,
        task_id: str | None = None,
        payload: dict | None = None,
    ) -> CopilotEvent:
        now = _now()
        with self._connect() as connection:
            owner = connection.execute(
                "SELECT 1 FROM copilot_streams WHERE tenant_id=? AND stream_id=?",
                (tenant_id, stream_id),
            ).fetchone()
            if owner is None:
                raise KeyError("Event stream not found")
            cursor = connection.execute(
                """INSERT INTO copilot_events(
                    stream_id, tenant_id, event_type, stage, status, title, detail,
                    task_id, payload, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    stream_id, tenant_id, event_type, stage, status, title, detail,
                    task_id, json.dumps(payload or {}, ensure_ascii=False), now,
                ),
            )
            event_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE copilot_streams SET updated_at=? WHERE tenant_id=? AND stream_id=?",
                (now, tenant_id, stream_id),
            )
        return CopilotEvent(
            event_id=event_id, stream_id=stream_id, event_type=event_type,
            stage=stage, status=status, title=title, detail=detail,
            task_id=task_id, payload=payload or {}, created_at=now,
        )

    def complete(self, tenant_id: str, stream_id: str, response: CopilotResponse) -> None:
        existing = self.events_after(tenant_id, stream_id)
        completed_stages = {
            event.stage
            for event in existing
            if event.event_type == "agent_completed"
        }
        for event in project_response_events(response):
            if event["event_type"] == "agent_completed" and event["stage"] in completed_stages:
                continue
            if any(
                current.event_type == event["event_type"]
                and current.stage == event["stage"]
                for current in existing
            ):
                continue
            self.append(tenant_id, stream_id, task_id=response.task_id, **event)
        payload = response.model_dump(mode="json")
        self.append(
            tenant_id, stream_id, event_type="response_ready", stage="presentation",
            status="completed", title="结果已生成", detail=response.assistant_message,
            task_id=response.task_id, payload={"response": payload},
        )
        with self._connect() as connection:
            connection.execute(
                """UPDATE copilot_streams SET status='completed', response_payload=?,
                    updated_at=? WHERE tenant_id=? AND stream_id=?""",
                (json.dumps(payload, ensure_ascii=False), _now(), tenant_id, stream_id),
            )

    def fail(self, tenant_id: str, stream_id: str, message: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stream = connection.execute(
                "SELECT status FROM copilot_streams WHERE tenant_id=? AND stream_id=?",
                (tenant_id, stream_id),
            ).fetchone()
            if stream is None:
                raise KeyError("Event stream not found")
            if stream["status"] != "running":
                return
            connection.execute(
                """INSERT INTO copilot_events(
                    stream_id, tenant_id, event_type, stage, status, title, detail,
                    task_id, payload, created_at
                ) VALUES(?, ?, 'stream_failed', 'system', 'failed', ?, ?, NULL, '{}', ?)""",
                (stream_id, tenant_id, "任务未完成", message[:500], now),
            )
            connection.execute(
                """UPDATE copilot_streams SET status='failed', error_message=?, updated_at=?
                    WHERE tenant_id=? AND stream_id=?""",
                (message[:1_000], now, tenant_id, stream_id),
            )

    def events_after(
        self, tenant_id: str, stream_id: str, event_id: int = 0
    ) -> list[CopilotEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM copilot_events WHERE tenant_id=? AND stream_id=?
                    AND event_id>? ORDER BY event_id""",
                (tenant_id, stream_id, event_id),
            ).fetchall()
        return [_event(row) for row in rows]

    def status(self, tenant_id: str, stream_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM copilot_streams WHERE tenant_id=? AND stream_id=?",
                (tenant_id, stream_id),
            ).fetchone()
        if row is None:
            raise KeyError("Event stream not found")
        return str(row["status"])

    def active_stream(self, tenant_id: str, conversation_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT stream_id, conversation_id, status FROM copilot_streams
                    WHERE tenant_id=? AND conversation_id=? AND status='running'
                    ORDER BY created_at DESC LIMIT 1""",
                (tenant_id, conversation_id),
            ).fetchone()
            if row is None:
                return None
            last_event = connection.execute(
                "SELECT MAX(event_id) AS last_event_id FROM copilot_events WHERE stream_id=?",
                (row["stream_id"],),
            ).fetchone()
        return {
            "stream_id": str(row["stream_id"]),
            "conversation_id": str(row["conversation_id"]),
            "status": "running",
            "events_url": f"/api/copilot/streams/{row['stream_id']}/events",
            "last_event_id": int(last_event["last_event_id"] or 0),
        }


def project_response_events(response: CopilotResponse) -> list[dict]:
    """Project recorded facts to public events; never expose chain-of-thought text."""

    events: list[dict] = []
    if response.intent:
        events.append({
            "event_type": "intent_recognized", "stage": "router", "status": "completed",
            "title": "需求已识别", "detail": f"已按“{response.intent.intent.value}”处理。",
            "payload": {"intent": response.intent.intent.value},
        })
    for step in response.action_summary.steps:
        if step.status not in {"completed", "failed"}:
            continue
        event_type = "review_revised" if "修" in step.title else "agent_completed"
        events.append({
            "event_type": event_type,
            "stage": step.agent_name,
            "status": "completed" if step.status == "completed" else "failed",
            "title": step.title,
            "detail": step.detail,
            "payload": {"tools": step.tool_names, "artifact_refs": step.artifact_refs},
        })
    if response.approval_required:
        events.append({
            "event_type": "approval_waiting", "stage": "approval", "status": "waiting",
            "title": "等待你的确认", "detail": "方案不会在确认前写入店铺。",
            "payload": {"execution_plan_hash": response.execution_plan_hash},
        })
    if response.store_modified:
        events.append({
            "event_type": "execution_completed", "stage": "execution", "status": "completed",
            "title": "店铺同步完成", "detail": "写入结果已完成回读验证。",
            "payload": {},
        })
    return events


def _event(row: sqlite3.Row) -> CopilotEvent:
    return CopilotEvent(
        event_id=row["event_id"], stream_id=row["stream_id"],
        event_type=row["event_type"], stage=row["stage"], status=row["status"],
        title=row["title"], detail=row["detail"], task_id=row["task_id"],
        payload=json.loads(row["payload"] or "{}"), created_at=row["created_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
