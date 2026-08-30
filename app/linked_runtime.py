from __future__ import annotations

from typing import Any

from app.browser.runtime import get_browser_runtime_status
from app.model.runtime import get_llm_runtime_status


REQUIRED_LLM_AGENTS = {
    "listing_agent",
    "strategy_agent",
    "review_agent",
}
REQUIRED_REACT_AGENTS = {"strategy_agent"}


def get_linked_runtime_status() -> dict[str, Any]:
    """Report whether the end-user flow is backed by real model and browser services."""
    llm = get_llm_runtime_status()
    browser = get_browser_runtime_status()
    issues: list[str] = []
    enabled_agents = set(llm.get("enabled_agents", []))
    react_enabled_agents = set(llm.get("react_enabled_agents", []))

    if llm.get("provider") != "deepseek" or not llm.get("real_llm_enabled"):
        issues.append("deepseek_not_enabled")
    if not llm.get("ready"):
        issues.extend(f"llm:{item}" for item in llm.get("issues", []))
    if not REQUIRED_LLM_AGENTS.issubset(enabled_agents):
        missing = sorted(REQUIRED_LLM_AGENTS - enabled_agents)
        issues.append(f"missing_llm_agents:{','.join(missing)}")
    if not REQUIRED_REACT_AGENTS.issubset(react_enabled_agents):
        missing = sorted(REQUIRED_REACT_AGENTS - react_enabled_agents)
        issues.append(f"missing_react_agents:{','.join(missing)}")
    if llm.get("fallback_mode") != "fail_closed":
        issues.append("llm_fallback_must_be_fail_closed")

    if browser.get("backend") != "playwright" or not browser.get(
        "real_browser_enabled"
    ):
        issues.append("playwright_not_enabled")
    if not browser.get("ready"):
        issues.extend(f"browser:{item}" for item in browser.get("issues", []))

    return {
        "ready": not issues,
        "issues": issues,
        "llm": llm,
        "browser": browser,
    }
