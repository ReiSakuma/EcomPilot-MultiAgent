from __future__ import annotations

from app.orchestration.state import TaskState


def constraint_satisfaction(state: TaskState) -> bool:
    review = state.agent_outputs.get("review_agent", {})
    return not review.get("violations")


def task_success(state: TaskState) -> bool:
    return state.status == "completed" and constraint_satisfaction(state)


def average_context_tokens(state: TaskState) -> float:
    usages = state.context_usage.values()
    if not usages:
        return 0.0
    return sum(float(item.get("token_estimate", 0)) for item in usages) / len(usages)


def tool_failure_rate(state: TaskState) -> float:
    if not state.tool_records:
        return 0.0
    failures = sum(1 for record in state.tool_records if record.get("status") == "failed")
    return failures / len(state.tool_records)


def side_effect_tool_count(state: TaskState) -> int:
    return sum(1 for record in state.tool_records if record.get("side_effect"))


def tool_retry_count(state: TaskState) -> int:
    return sum(max(0, int(record.get("attempt_count", 1)) - 1) for record in state.tool_records)


def idempotent_replay_count(state: TaskState) -> int:
    return sum(1 for record in state.tool_records if record.get("idempotent_replay"))


def tool_validation_failure_count(state: TaskState) -> int:
    validation_errors = {
        "ToolParameterError",
        "ToolResultValidationError",
        "ToolPermissionError",
        "ToolApprovalRequiredError",
    }
    return sum(1 for record in state.tool_records if record.get("error_type") in validation_errors)


def model_call_count(state: TaskState) -> int:
    return len(state.model_records)


def market_sample_score(state: TaskState) -> float:
    market = state.agent_outputs.get("market_agent", {})
    sample_size = market.get("sample_size", {})
    competitors = float(sample_size.get("competitors", 0))
    reviews = float(sample_size.get("reviews", 0))
    return min(1.0, competitors / 10) * 0.6 + min(1.0, reviews / 20) * 0.4


def execution_verification_rate(state: TaskState) -> float:
    browser = state.agent_outputs.get("browser_agent", {})
    if not browser:
        return 0.0
    verification = browser.get("verification", {})
    return 1.0 if verification.get("verified") else 0.0
