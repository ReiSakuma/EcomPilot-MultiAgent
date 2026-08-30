from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LlmPolicy:
    enabled_agents: set[str]
    react_enabled_agents: set[str] = field(default_factory=set)
    require_json: bool = True
    allow_side_effects: bool = False
    fallback_mode: str = "deterministic"
    max_calls_per_agent: int = 2
    max_repair_attempts: int = 1
    react_max_steps: int = 2
    react_max_tool_calls: int = 2
    react_timeout_seconds: float = 60.0
    react_max_identical_actions: int = 1
    react_input_token_budget: int = 12000
    react_max_output_tokens: int = 1600
    react_compression_trigger_ratio: float = 0.70

    def enabled_for(self, agent_name: str) -> bool:
        return agent_name in self.enabled_agents

    def react_enabled_for(self, agent_name: str) -> bool:
        return agent_name in self.react_enabled_agents


def load_llm_policy() -> LlmPolicy:
    raw_agents = os.getenv("ECOMPILOT_LLM_AGENTS", "")
    enabled_agents = {item.strip() for item in raw_agents.split(",") if item.strip()}
    raw_react_agents = os.getenv("ECOMPILOT_REACT_AGENTS", "")
    react_enabled_agents = {
        item.strip() for item in raw_react_agents.split(",") if item.strip()
    }
    fallback_mode = os.getenv("ECOMPILOT_LLM_FALLBACK", "deterministic")
    if fallback_mode not in {"deterministic", "fail_closed"}:
        fallback_mode = "fail_closed"
    return LlmPolicy(
        enabled_agents=enabled_agents,
        react_enabled_agents=react_enabled_agents,
        fallback_mode=fallback_mode,
        max_calls_per_agent=max(1, int(os.getenv("ECOMPILOT_LLM_MAX_CALLS_PER_AGENT", "2"))),
        max_repair_attempts=max(
            0, min(1, int(os.getenv("ECOMPILOT_LLM_MAX_REPAIR_ATTEMPTS", "1")))
        ),
        react_max_steps=max(
            1, min(2, int(os.getenv("ECOMPILOT_REACT_MAX_STEPS", "2")))
        ),
        react_max_tool_calls=max(
            1, min(2, int(os.getenv("ECOMPILOT_REACT_MAX_TOOL_CALLS", "2")))
        ),
        react_timeout_seconds=max(
            1.0, min(600.0, float(os.getenv("ECOMPILOT_REACT_TIMEOUT_SECONDS", "60")))
        ),
        react_max_identical_actions=max(
            1,
            min(3, int(os.getenv("ECOMPILOT_REACT_MAX_IDENTICAL_ACTIONS", "1"))),
        ),
        react_input_token_budget=max(
            256, min(100000, int(os.getenv("ECOMPILOT_REACT_INPUT_TOKEN_BUDGET", "12000")))
        ),
        react_max_output_tokens=max(
            128, min(16000, int(os.getenv("ECOMPILOT_REACT_MAX_OUTPUT_TOKENS", "1600")))
        ),
        react_compression_trigger_ratio=max(
            0.40,
            min(
                0.95,
                float(os.getenv("ECOMPILOT_REACT_COMPRESSION_TRIGGER_RATIO", "0.70")),
            ),
        ),
    )
