from __future__ import annotations

from typing import Any

from app.model.adapter import ModelAdapter, ModelResponse


def completed_model_record(
    response: ModelResponse,
    *,
    agent_name: str,
    purpose: str,
    status: str = "completed",
    **extra: Any,
) -> dict[str, Any]:
    """Normalize stage-level token and termination telemetry for checkpoints."""

    return response.model_dump(mode="json") | {
        "agent_name": agent_name,
        "purpose": purpose,
        "stage": purpose,
        "status": status,
        "input_token_estimate": response.request_input_tokens_estimate,
        "actual_input_tokens": response.input_tokens,
        "reserved_output_tokens": response.requested_max_output_tokens,
        "actual_output_tokens": response.output_tokens,
        "finish_reason": response.finish_reason,
        **extra,
    }


def failed_model_record(
    adapter: ModelAdapter,
    exc: Exception,
    *,
    agent_name: str,
    purpose: str,
    error_limit: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    error = str(exc)
    if error_limit is not None:
        error = error[:error_limit]
    return {
        "call_id": getattr(exc, "model_call_id", None),
        "provider": adapter.provider,
        "model": adapter.model,
        "agent_name": agent_name,
        "purpose": purpose,
        "stage": purpose,
        "status": "failed",
        "error_type": type(exc).__name__,
        "error": error,
        "retryable": bool(getattr(exc, "retryable", False)),
        "request_attempts": int(
            getattr(exc, "model_request_attempts", 1) or 1
        ),
        "transport_attempts": int(
            getattr(exc, "model_request_attempts", 1) or 1
        ),
        "input_token_estimate": int(
            getattr(exc, "model_input_token_estimate", 0) or 0
        ),
        "actual_input_tokens": None,
        "reserved_output_tokens": int(
            getattr(exc, "model_requested_max_output_tokens", 0) or 0
        ),
        "actual_output_tokens": None,
        "finish_reason": getattr(exc, "model_finish_reason", None),
        **extra,
    }
