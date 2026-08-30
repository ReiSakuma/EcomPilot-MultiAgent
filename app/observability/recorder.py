from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from app.config import TRACE_DIR
from app.public_progress import publish_trace_event
from app.observability.schemas import TraceEvent, TraceEventType


SENSITIVE_KEY_PARTS = ("api_key", "authorization", "password", "secret")


class TraceRecorder:
    """Thread-safe JSONL event recorder shared by the workflow, tools, and models."""

    def __init__(self, run_id: str, trace_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.trace_dir = trace_dir or TRACE_DIR
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.trace_dir / f"{run_id}.jsonl"
        self._lock = Lock()
        self._sequence = 0

    def record_event(
        self,
        task_id: str,
        event_type: TraceEventType | str,
        component_type: str,
        component_name: str,
        step: str,
        *,
        status: str | None = None,
        duration_ms: float | None = None,
        details: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> TraceEvent:
        with self._lock:
            self._sequence += 1
            event = TraceEvent(
                event_id=f"evt_{uuid4().hex[:12]}",
                sequence=self._sequence,
                run_id=self.run_id,
                task_id=task_id,
                event_type=TraceEventType(event_type),
                timestamp=datetime.now(timezone.utc),
                component_type=component_type,
                component_name=component_name,
                step=step,
                status=status,
                duration_ms=duration_ms,
                details=_sanitize(details or {}),
                error=_sanitize(error) if error else None,
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        publish_trace_event(event)
        return event

    def record(self, task_id: str, step: str, agent_name: str, event: dict[str, Any]) -> None:
        """Compatibility entry point for earlier executor calls."""
        event_type = {
            "plan": TraceEventType.plan_created,
            "working_memory": TraceEventType.working_memory,
            "loop_detection": TraceEventType.error,
        }.get(step, TraceEventType.agent_completed)
        self.record_event(
            task_id,
            event_type,
            "agent",
            agent_name,
            step,
            status=event.get("status"),
            duration_ms=event.get("elapsed_ms"),
            details=event,
            error={"message": str(event.get("error"))} if event.get("error") else None,
        )


def _sanitize(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if hasattr(value, "model_dump"):
        return _sanitize(value.model_dump(mode="json"))
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + "...[truncated]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
