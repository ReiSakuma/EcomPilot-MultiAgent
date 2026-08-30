from __future__ import annotations


MODEL_PRICING_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-5-mini": {"input": 0.25, "output": 2.0},
    "gpt-5-nano": {"input": 0.05, "output": 0.4},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6},
    # Conservative cache-miss estimates from the DeepSeek pricing page on 2026-08-24.
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
    "local-rule-v6": {"input": 0.0, "output": 0.0},
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING_PER_1M_TOKENS.get(model)
    if pricing is None:
        return 0.0
    return round(
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"],
        8,
    )
