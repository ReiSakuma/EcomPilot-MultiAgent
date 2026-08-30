from __future__ import annotations


def compress_text(text: str, max_chars: int = 800) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
