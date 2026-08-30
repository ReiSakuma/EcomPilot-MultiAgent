from __future__ import annotations

import json
from typing import Iterable

from app.context.schemas import BudgetedContextItem, ContextBudgetDecision
from app.context.token_budget import estimate_tokens


class ContextBudgetManager:
    """Deterministic context admission. Models never decide what security facts survive."""

    def __init__(
        self,
        *,
        context_window_tokens: int = 32768,
        reserve_ratio: float = 0.30,
        compression_ratio: float = 0.70,
        split_ratio: float = 0.85,
    ) -> None:
        if context_window_tokens < 512:
            raise ValueError("context_window_tokens must be at least 512")
        if not 0.30 <= reserve_ratio < 0.80:
            raise ValueError("reserve_ratio must preserve at least 30% of the window")
        self.context_window_tokens = context_window_tokens
        self.reserve_ratio = reserve_ratio
        self.compression_ratio = compression_ratio
        self.split_ratio = split_ratio

    def decide(
        self,
        items: Iterable[BudgetedContextItem],
        *,
        next_input: str = "",
    ) -> ContextBudgetDecision:
        material = [item.model_copy(deep=True) for item in items]
        for item in material:
            item.token_estimate = item.token_estimate or estimate_tokens(
                json.dumps(item.content, ensure_ascii=False, sort_keys=True)
            )
        next_tokens = estimate_tokens(next_input) if next_input else 0
        reserved = max(1, int(self.context_window_tokens * self.reserve_ratio))
        capacity = self.context_window_tokens - reserved
        raw_tokens = sum(item.token_estimate for item in material) + next_tokens
        compression = raw_tokens > int(capacity * self.compression_ratio)
        split = raw_tokens + reserved > int(self.context_window_tokens * self.split_ratio)

        selected: list[BudgetedContextItem] = []
        dropped: list[str] = []
        used = next_tokens
        for priority in ("P0", "P1", "P2", "P3", "P4"):
            for item in (candidate for candidate in material if candidate.priority == priority):
                if priority in {"P0", "P1"}:
                    item.action = "keep"
                    selected.append(item)
                    used += item.token_estimate
                    continue
                if not compression and used + item.token_estimate <= capacity:
                    item.action = "keep"
                    selected.append(item)
                    used += item.token_estimate
                    continue
                if priority == "P2" and used + item.token_estimate <= capacity:
                    item.action = "structure"
                    selected.append(item)
                    used += item.token_estimate
                    continue
                if priority == "P3" and used < capacity:
                    compact = _compact(item.content, max_chars=max(80, (capacity - used) * 4))
                    item.content = compact
                    item.token_estimate = estimate_tokens(json.dumps(compact, ensure_ascii=False))
                    if used + item.token_estimate <= capacity:
                        item.action = "summarize"
                        selected.append(item)
                        used += item.token_estimate
                        continue
                item.action = "drop"
                dropped.append(item.item_id)

        reason = (
            "hard_threshold_split_or_subtask_required" if split else
            "soft_threshold_layered_compression" if compression else
            "within_budget"
        )
        return ContextBudgetDecision(
            context_window_tokens=self.context_window_tokens,
            reserved_tokens=reserved,
            input_capacity_tokens=capacity,
            next_input_tokens=next_tokens,
            selected_tokens=used,
            compression_required=compression,
            split_required=split,
            selected=selected,
            dropped_item_ids=dropped,
            reason=reason,
        )


def _compact(value: object, *, max_chars: int) -> object:
    if isinstance(value, list):
        return value[-6:]
    if isinstance(value, dict):
        return {key: value[key] for key in list(value)[-12:]}
    text = str(value)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
