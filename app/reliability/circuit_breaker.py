from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock

from app.reliability.models import CircuitSnapshot


class CircuitOpenError(RuntimeError):
    safe_to_retry = False


class CircuitBreakerRegistry:
    def __init__(self, recovery_seconds: float = 30.0) -> None:
        self.recovery_seconds = recovery_seconds
        self._circuits: dict[str, CircuitSnapshot] = {}
        self._lock = RLock()

    def before_call(self, key: str, *, threshold: int = 3) -> CircuitSnapshot:
        with self._lock:
            snapshot = self._circuits.setdefault(
                key, CircuitSnapshot(key=key, threshold=threshold)
            )
            if snapshot.state == "open":
                assert snapshot.opened_at is not None
                elapsed = datetime.now(timezone.utc) - snapshot.opened_at
                if elapsed >= timedelta(seconds=self.recovery_seconds):
                    snapshot.state = "half_open"
                else:
                    raise CircuitOpenError(f"Circuit '{key}' is open")
            return snapshot.model_copy(deep=True)

    def success(self, key: str) -> None:
        with self._lock:
            snapshot = self._circuits.get(key)
            if snapshot is None:
                return
            snapshot.state = "closed"
            snapshot.consecutive_failures = 0
            snapshot.opened_at = None
            snapshot.last_signature = None

    def failure(self, key: str, signature: str, *, threshold: int = 3) -> CircuitSnapshot:
        with self._lock:
            snapshot = self._circuits.setdefault(
                key, CircuitSnapshot(key=key, threshold=threshold)
            )
            if snapshot.last_signature == signature:
                snapshot.consecutive_failures += 1
            else:
                snapshot.consecutive_failures = 1
            snapshot.last_signature = signature
            if snapshot.consecutive_failures >= snapshot.threshold:
                snapshot.state = "open"
                snapshot.opened_at = datetime.now(timezone.utc)
            return snapshot.model_copy(deep=True)

    def snapshots(self) -> list[CircuitSnapshot]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._circuits.values()]
