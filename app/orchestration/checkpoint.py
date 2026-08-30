from __future__ import annotations

import json
import re
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import CHECKPOINT_DIR
from app.model.promotion_migration import migrate_checkpoint_payload
from app.orchestration.state import TaskState
from app.presentation import build_task_presentation


TASK_ID_PATTERN = re.compile(r"^task_[A-Za-z0-9_-]+$")


class CheckpointError(RuntimeError):
    pass


class CheckpointNotFoundError(CheckpointError):
    pass


class InvalidTaskIdError(CheckpointError):
    pass


class StaleCheckpointError(CheckpointError):
    pass


class CheckpointStore:
    _lock = RLock()

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or CHECKPOINT_DIR
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, state: TaskState) -> int:
        path = self._path(state.task_id)
        with self._lock:
            state.checkpoint_version += 1
            temporary = path.with_suffix(".tmp")
            temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
        return state.checkpoint_version

    def load(
        self, task_id: str, expected_version: int | None = None
    ) -> TaskState:
        path = self._path(task_id)
        with self._lock:
            if not path.exists():
                raise CheckpointNotFoundError(f"Checkpoint not found for '{task_id}'")
            try:
                payload = migrate_checkpoint_payload(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                state = TaskState.model_validate(payload)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                raise CheckpointError(f"Checkpoint for '{task_id}' is invalid: {exc}") from exc
        if expected_version is not None and state.checkpoint_version != expected_version:
            raise StaleCheckpointError(
                f"Expected checkpoint version {expected_version}, found {state.checkpoint_version}"
            )
        return state

    def get_metadata(self, task_id: str) -> dict[str, Any]:
        return self._metadata(self.load(task_id))

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        paths = sorted(
            self.directory.glob("task_*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[: max(1, min(limit, 200))]
        metadata: list[dict[str, Any]] = []
        for path in paths:
            try:
                state = self.load(path.stem)
            except CheckpointError:
                continue
            metadata.append(self._metadata(state))
        return metadata

    def _path(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise InvalidTaskIdError(
                "task_id must start with 'task_' and contain only safe characters"
            )
        return self.directory / f"{task_id}.json"

    @staticmethod
    def _metadata(state: TaskState) -> dict[str, Any]:
        presentation = build_task_presentation(state)
        return {
            "task_id": state.task_id,
            "conversation_id": state.conversation_id,
            "turn_id": state.turn_id,
            "intent": state.intent,
            "entity_refs": state.entity_refs,
            "run_id": state.run_id,
            "parent_run_id": state.parent_run_id,
            "status": state.status,
            "outcome": presentation.outcome.value,
            "failure": (
                presentation.failure.model_dump(mode="json")
                if presentation.failure
                else None
            ),
            "degradation_count": len(presentation.degradations),
            "checkpoint_version": state.checkpoint_version,
            "resume_count": state.resume_count,
            "updated_at": state.updated_at.isoformat(),
            "node_statuses": {
                node_id: node.status.value for node_id, node in state.nodes.items()
            },
            "recoverable": state.status in {
                "waiting_for_approval", "waiting_for_input", "failed"
            },
        }
