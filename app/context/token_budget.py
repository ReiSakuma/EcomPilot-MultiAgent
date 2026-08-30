from __future__ import annotations


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
