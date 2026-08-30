from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import TRACE_DIR
from app.observability.schemas import RunSummary


RUN_ID_PATTERN = re.compile(r"^run_[A-Za-z0-9_-]+$")


class TraceNotFoundError(FileNotFoundError):
    pass


class InvalidRunIdError(ValueError):
    pass


class TraceStore:
    def __init__(self, trace_dir: Path | None = None) -> None:
        self.trace_dir = trace_dir or TRACE_DIR
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        paths = sorted(
            self.trace_dir.glob("run_*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[: max(1, min(limit, 200))]
        return [self._summarize(path.stem, self._read_path(path)).model_dump(mode="json") for path in paths]

    def get_run(self, run_id: str) -> dict[str, Any]:
        events = self._read(run_id)
        return {
            "summary": self._summarize(run_id, events).model_dump(mode="json"),
            "events": events,
        }

    def get_summary(self, run_id: str) -> dict[str, Any]:
        events = self._read(run_id)
        return self._summarize(run_id, events).model_dump(mode="json")

    def _read(self, run_id: str) -> list[dict[str, Any]]:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise InvalidRunIdError("run_id must start with 'run_' and contain only safe characters")
        path = self.trace_dir / f"{run_id}.jsonl"
        if not path.exists():
            raise TraceNotFoundError(run_id)
        return self._read_path(path)

    @staticmethod
    def _read_path(path: Path) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {
                    "event_type": "error",
                    "step": "trace_parse",
                    "status": "failed",
                    "error": {"message": f"invalid JSONL at line {line_number}"},
                }
            events.append(event)
        return events

    @staticmethod
    def _summarize(run_id: str, events: list[dict[str, Any]]) -> RunSummary:
        if not events:
            return RunSummary(run_id=run_id)
        first = events[0]
        last = events[-1]
        started_at = _parse_time(first.get("timestamp"))
        ended_at = _parse_time(last.get("timestamp"))
        duration_ms = None
        if started_at and ended_at:
            duration_ms = round((ended_at - started_at).total_seconds() * 1000, 2)

        status = "unknown"
        goal = ""
        parent_run_id = None
        resume_count = 0
        agent_statuses: dict[str, str] = {}
        for event in events:
            event_type = event.get("event_type", "")
            details = event.get("details") or {}
            if event_type == "run_started":
                goal = str(details.get("goal", ""))
                parent_run_id = details.get("parent_run_id")
                resume_count = int(details.get("resume_count", 0))
            if event_type == "agent_completed":
                name = str(event.get("component_name") or event.get("agent_name") or "unknown")
                agent_statuses[name] = str(event.get("status") or details.get("status") or "unknown")
            if event_type == "run_completed":
                status = str(event.get("status") or details.get("status") or status)
        if status == "unknown":
            status = str(last.get("status") or last.get("details", {}).get("status") or "unknown")

        return RunSummary(
            run_id=run_id,
            task_id=str(first.get("task_id", "")),
            goal=goal,
            status=status,
            parent_run_id=parent_run_id,
            resume_count=resume_count,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            event_count=len(events),
            agent_event_count=sum(1 for event in events if event.get("event_type") == "agent_completed"),
            tool_call_count=sum(1 for event in events if event.get("event_type") == "tool_call"),
            model_call_count=sum(1 for event in events if event.get("event_type") == "model_call"),
            error_count=sum(
                1
                for event in events
                if event.get("event_type") == "error"
                or (
                    event.get("event_type") in {"tool_call", "model_call"}
                    and event.get("status") == "failed"
                )
            ),
            failed_status_event_count=sum(
                1 for event in events if event.get("status") in {"failed", "retry_scheduled"}
            ),
            guardrail_event_count=sum(
                1
                for event in events
                if event.get("event_type") == "approval_waiting"
                or event.get("status") in {"requires_review", "requires_revision"}
            ),
            agent_statuses=agent_statuses,
        )


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
