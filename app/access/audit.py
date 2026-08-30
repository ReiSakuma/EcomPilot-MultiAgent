from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any


class AccessAuditStore:
    _lock = RLock()
    _records: list[dict[str, Any]] = []
    _max_records = 5000

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._records.append(
                {**record, "created_at": datetime.now(timezone.utc).isoformat()}
            )
            if len(self._records) > self._max_records:
                del self._records[: len(self._records) - self._max_records]

    def read(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._records[-max(1, min(limit, 200)) :]))

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
