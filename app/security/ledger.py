from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

import fcntl

from pydantic import BaseModel, ConfigDict, Field

from app.config import SECURITY_LEDGER_PATH


GENESIS_HASH = "0" * 64


class SecurityLedgerError(RuntimeError):
    pass


class SecurityLedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: f"sec_{uuid4().hex[:12]}")
    event_type: str
    token_id: str
    task_id: str
    delegation_id: str
    capability_id: str
    agent_name: str
    tool_name: str | None = None
    decision: str
    reason: str | None = None
    use_count: int | None = None
    max_uses: int | None = None
    created_at: datetime
    previous_hash: str
    record_hash: str


class SecurityLedger:
    """Append-only JSONL audit ledger protected by a SHA-256 hash chain."""

    _lock = RLock()
    _head_cache: dict[Path, tuple[int, int, int, str, int]] = {}

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SECURITY_LEDGER_PATH

    def append(self, **payload: Any) -> SecurityLedgerEntry:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._file_lock(exclusive=True):
                previous_hash, entry_count = self._trusted_head(lock_held=True)
                base = {
                    "event_id": f"sec_{uuid4().hex[:12]}",
                    **payload,
                    "created_at": datetime.now(timezone.utc),
                    "previous_hash": previous_hash,
                }
                draft = SecurityLedgerEntry(**base, record_hash=GENESIS_HASH)
                canonical_payload = draft.model_dump(
                    mode="python", exclude={"record_hash"}
                )
                record_hash = _record_hash(canonical_payload)
                entry = draft.model_copy(update={"record_hash": record_hash})
                encoded = (entry.model_dump_json() + "\n").encode("utf-8")
                descriptor = os.open(
                    self.path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    written = os.write(descriptor, encoded)
                    if written != len(encoded):
                        raise SecurityLedgerError("Incomplete security ledger append")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                stat = self.path.stat()
                self._head_cache[self.path.resolve()] = (
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    entry.record_hash,
                    entry_count + 1,
                )
                return entry

    def read(self, *, task_id: str | None = None, limit: int | None = None) -> list[SecurityLedgerEntry]:
        if not self.path.exists():
            return []
        with self._file_lock(exclusive=False):
            entries = self._read_unlocked()
        if task_id is not None:
            entries = [entry for entry in entries if entry.task_id == task_id]
        if limit is not None:
            entries = entries[-limit:]
        return entries

    def verify_integrity(self) -> dict[str, Any]:
        try:
            entries = self.read()
        except SecurityLedgerError as exc:
            return {"valid": False, "entry_count": 0, "invalid_index": None, "error": str(exc)}
        result = self._verify_entries(entries)
        if result["valid"] and self.path.exists():
            stat = self.path.stat()
            with self._lock:
                self._head_cache[self.path.resolve()] = (
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    result["head_hash"],
                    result["entry_count"],
                )
        return result

    def _trusted_head(self, *, lock_held: bool = False) -> tuple[str, int]:
        resolved = self.path.resolve()
        if not self.path.exists():
            self._head_cache.pop(resolved, None)
            return GENESIS_HASH, 0
        stat = self.path.stat()
        cached = self._head_cache.get(resolved)
        if cached and cached[:3] == (
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ):
            return cached[3], cached[4]
        entries = self._read_unlocked() if lock_held else self.read()
        integrity = self._verify_entries(entries)
        if not integrity["valid"]:
            raise SecurityLedgerError(
                f"Security ledger integrity check failed at entry {integrity['invalid_index']}"
            )
        self._head_cache[resolved] = (
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            integrity["head_hash"],
            integrity["entry_count"],
        )
        return integrity["head_hash"], integrity["entry_count"]

    def _read_unlocked(self) -> list[SecurityLedgerEntry]:
        entries: list[SecurityLedgerEntry] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entries.append(SecurityLedgerEntry.model_validate_json(line))
                except Exception as exc:
                    raise SecurityLedgerError(
                        f"Invalid security ledger entry at line {line_number}"
                    ) from exc
        return entries

    @contextmanager
    def _file_lock(self, *, exclusive: bool):
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), mode)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _verify_entries(entries: list[SecurityLedgerEntry]) -> dict[str, Any]:
        previous_hash = GENESIS_HASH
        for index, entry in enumerate(entries):
            payload = entry.model_dump(mode="python", exclude={"record_hash"})
            if entry.previous_hash != previous_hash or _record_hash(payload) != entry.record_hash:
                return {
                    "valid": False,
                    "entry_count": len(entries),
                    "invalid_index": index,
                    "error": "hash_chain_mismatch",
                }
            previous_hash = entry.record_hash
        return {
            "valid": True,
            "entry_count": len(entries),
            "invalid_index": None,
            "head_hash": previous_hash,
        }


def build_task_security_summary(task_id: str, ledger: SecurityLedger | None = None) -> dict[str, Any]:
    ledger = ledger or SecurityLedger()
    entries = ledger.read(task_id=task_id)
    integrity = ledger.verify_integrity()
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.event_type] = counts.get(entry.event_type, 0) + 1
    return {
        "task_id": task_id,
        "integrity": integrity,
        "summary": {
            "issued": counts.get("token_issued", 0),
            "allowed": counts.get("tool_allowed", 0),
            "denied": counts.get("tool_denied", 0),
            "revoked": counts.get("token_revoked", 0),
        },
        "events": [entry.model_dump(mode="json") for entry in entries],
    }


def _record_hash(payload: dict[str, Any]) -> str:
    normalized = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in payload.items()
    }
    encoded = json.dumps(
        normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
