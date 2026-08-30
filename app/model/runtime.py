from __future__ import annotations

from typing import Any

from app.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_REQUEST_BUDGET_SECONDS,
    LLM_TIMEOUT_SECONDS,
    WORKFLOW_NODE_TIMEOUT_SECONDS,
    WORKFLOW_TIMEOUT_SECONDS,
)
from app.model.policy import load_llm_policy


SUPPORTED_PROVIDERS = {"deterministic", "openai", "openai-compatible", "deepseek"}
SUPPORTED_LLM_AGENTS = {
    "market_agent",
    "listing_agent",
    "strategy_agent",
    "review_agent",
    "analytics_agent",
}
SUPPORTED_REACT_AGENTS = {"market_agent", "strategy_agent", "analytics_agent"}


def get_llm_runtime_status() -> dict[str, Any]:
    policy = load_llm_policy()
    issues: list[str] = []
    unknown_agents = sorted(policy.enabled_agents - SUPPORTED_LLM_AGENTS)
    unknown_react_agents = sorted(
        policy.react_enabled_agents - SUPPORTED_REACT_AGENTS
    )
    if LLM_PROVIDER not in SUPPORTED_PROVIDERS:
        issues.append(f"unsupported_provider:{LLM_PROVIDER}")
    if unknown_agents:
        issues.append(f"unsupported_agents:{','.join(unknown_agents)}")
    if unknown_react_agents:
        issues.append(
            f"unsupported_react_agents:{','.join(unknown_react_agents)}"
        )
    react_without_llm = sorted(
        policy.react_enabled_agents - policy.enabled_agents
    )
    if react_without_llm:
        issues.append(
            f"react_agents_not_llm_enabled:{','.join(react_without_llm)}"
        )
    if policy.react_enabled_agents and LLM_PROVIDER != "deepseek":
        issues.append("react_provider_must_be_deepseek")
    if policy.react_enabled_agents and policy.max_calls_per_agent < 2:
        issues.append("react_model_call_budget_below_2")
    if (
        policy.react_enabled_agents
        and policy.react_timeout_seconds > WORKFLOW_NODE_TIMEOUT_SECONDS
    ):
        issues.append("react_timeout_above_node_timeout")

    real_llm_enabled = LLM_PROVIDER in {"openai", "openai-compatible", "deepseek"}
    if real_llm_enabled and not LLM_API_KEY:
        issues.append("missing_api_key")
    if real_llm_enabled and not policy.enabled_agents:
        issues.append("no_llm_agents_enabled")
    if (
        real_llm_enabled
        and WORKFLOW_NODE_TIMEOUT_SECONDS <= LLM_REQUEST_BUDGET_SECONDS
    ):
        issues.append("node_timeout_below_llm_request_budget")

    return {
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "api_key_configured": bool(LLM_API_KEY),
        "enabled_agents": sorted(policy.enabled_agents),
        "supported_agents": sorted(SUPPORTED_LLM_AGENTS),
        "react_enabled_agents": sorted(policy.react_enabled_agents),
        "supported_react_agents": sorted(SUPPORTED_REACT_AGENTS),
        "fallback_mode": policy.fallback_mode,
        "timeout_seconds": LLM_TIMEOUT_SECONDS,
        "max_retries": LLM_MAX_RETRIES,
        "max_output_tokens": LLM_MAX_OUTPUT_TOKENS,
        "llm_request_budget_seconds": LLM_REQUEST_BUDGET_SECONDS,
        "node_timeout_seconds": WORKFLOW_NODE_TIMEOUT_SECONDS,
        "workflow_timeout_seconds": WORKFLOW_TIMEOUT_SECONDS,
        "max_calls_per_agent": policy.max_calls_per_agent,
        "max_repair_attempts": policy.max_repair_attempts,
        "react_max_steps": policy.react_max_steps,
        "react_max_tool_calls": policy.react_max_tool_calls,
        "react_timeout_seconds": policy.react_timeout_seconds,
        "react_max_identical_actions": policy.react_max_identical_actions,
        "real_llm_enabled": real_llm_enabled,
        "ready": not issues,
        "issues": issues,
    }
