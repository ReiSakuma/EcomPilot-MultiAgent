from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import IDEMPOTENCY_DIR, IDEMPOTENCY_FILE


class IdempotencyConflictError(ValueError):
    pass


class IdempotencyInProgressError(RuntimeError):
    pass


class IdempotencyStore:
    _lock = RLock()

    def __init__(
        self, path: Path | None = None, *, namespace: str | None = None
    ) -> None:
        self.namespace = namespace
        self.path = path or (
            IDEMPOTENCY_DIR / namespace / "records.json"
            if namespace
            else IDEMPOTENCY_FILE
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, fingerprint: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            record = self._load().get(key)
            if record is None:
                return None
            if fingerprint is not None and record.get("fingerprint") != fingerprint:
                raise IdempotencyConflictError(
                    f"Idempotency key '{key}' was already used with different arguments"
                )
            value = record.get("value")
            return dict(value) if isinstance(value, dict) else None

    def put(
        self, key: str, value: dict[str, Any], fingerprint: str | None = None
    ) -> None:
        with self._lock:
            records = self._load()
            records[key] = {
                "fingerprint": fingerprint or fingerprint_payload(value),
                "status": "completed",
                "value": value,
            }
            self._save(records)

    def execute_once(
        self,
        key: str,
        payload: dict[str, Any],
        operation: Callable[[], dict[str, Any]],
    ) -> tuple[bool, dict[str, Any]]:
        fingerprint = fingerprint_payload(payload)
        with self._lock:
            records = self._load()
            existing = records.get(key)
            if existing is not None:
                if existing.get("fingerprint") != fingerprint:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{key}' was already used with different arguments"
                    )
                if existing.get("status") == "completed":
                    return True, dict(existing["value"])
                if existing.get("status") == "in_progress":
                    raise IdempotencyInProgressError(
                        f"Idempotent operation '{key}' is already in progress"
                    )

            records[key] = {"fingerprint": fingerprint, "status": "in_progress", "value": None}
            self._save(records)
            try:
                value = operation()
            except Exception as exc:
                records[key] = {
                    "fingerprint": fingerprint,
                    "status": "failed",
                    "value": None,
                    "error": str(exc),
                }
                self._save(records)
                raise
            records[key] = {"fingerprint": fingerprint, "status": "completed", "value": value}
            self._save(records)
            return False, value

    def clear(self) -> None:
        with self._lock:
            self._save({})

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self, records: dict[str, dict[str, Any]]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)


def fingerprint_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
