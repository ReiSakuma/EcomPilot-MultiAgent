from __future__ import annotations

from collections import deque
from typing import Any


class ShortTermMemory:
    def __init__(self, max_items: int = 20) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=max_items)

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def recent(self) -> list[dict[str, Any]]:
        return list(self.events)
