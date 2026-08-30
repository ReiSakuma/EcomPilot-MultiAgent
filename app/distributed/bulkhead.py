from __future__ import annotations

from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from typing import Iterator


DEFAULT_CAPACITIES = {
    "workflow": 8,
    "model": 8,
    "sql": 12,
    "read_tool": 16,
    "write_tool": 4,
    "browser": 2,
}


class BulkheadFullError(RuntimeError):
    pass


class BulkheadRegistry:
    """Process-local capacity isolation; durable ownership remains in the queue."""

    def __init__(self, capacities: dict[str, int] | None = None) -> None:
        self.capacities = {**DEFAULT_CAPACITIES, **(capacities or {})}
        self._semaphores = {
            name: BoundedSemaphore(max(1, capacity))
            for name, capacity in self.capacities.items()
        }
        self._active = {name: 0 for name in self.capacities}
        self._lock = Lock()

    @contextmanager
    def acquire(self, pool: str, *, blocking: bool = False) -> Iterator[None]:
        semaphore = self._semaphores[pool]
        acquired = semaphore.acquire(blocking=blocking)
        if not acquired:
            raise BulkheadFullError(f"Worker pool '{pool}' is at capacity")
        with self._lock:
            self._active[pool] += 1
        try:
            yield
        finally:
            with self._lock:
                self._active[pool] -= 1
            semaphore.release()

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                name: {
                    "capacity": capacity,
                    "active": self._active[name],
                    "available": capacity - self._active[name],
                }
                for name, capacity in self.capacities.items()
            }


GLOBAL_BULKHEADS = BulkheadRegistry()
