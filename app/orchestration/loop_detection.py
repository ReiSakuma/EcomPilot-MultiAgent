from __future__ import annotations

import hashlib
import json
from typing import Any


class RepeatCallDetector:
    def __init__(self, max_repeats: int = 2) -> None:
        self.max_repeats = max_repeats
        self._counts: dict[str, int] = {}

    def seen_too_often(self, namespace: str, payload: dict[str, Any]) -> bool:
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{namespace}:{normalized}".encode("utf-8")).hexdigest()
        self._counts[digest] = self._counts.get(digest, 0) + 1
        return self._counts[digest] > self.max_repeats
