from __future__ import annotations

from pydantic import ValidationError

from app.model.adapter import (
    ModelIncompleteError,
    ModelRateLimitError,
    ModelTransientError,
)
from app.model.contracts import ListingModelOutput
from app.model.structured import StructuredOutputError, parse_json_object


MALFORMED_OUTPUTS = {
    "missing_field": '{"title":"合规无线耳机","keywords":["无线耳机"]}',
    "wrong_type": (
        '{"title":"合规无线耳机","keywords":"无线耳机",'
        '"bullets":["长续航"],"compliance_notes":[]}'
    ),
    "non_json": "这里是商品文案，不是 JSON。",
}


def run_model_protocol_case(scenario: str) -> str:
    """Exercise local validation and provider-error classification without external calls."""
    if scenario in MALFORMED_OUTPUTS:
        try:
            ListingModelOutput.model_validate(parse_json_object(MALFORMED_OUTPUTS[scenario]))
        except (StructuredOutputError, ValidationError):
            return "structured_validation_failed"
        return "unexpected_success"
    if scenario == "timeout":
        error = ModelTransientError("simulated timeout")
        return "retryable_transient" if error.retryable else "unexpected_non_retryable"
    if scenario == "rate_limit":
        error = ModelRateLimitError("simulated 429")
        return "retryable_rate_limit" if error.retryable else "unexpected_non_retryable"
    if scenario == "incomplete":
        error = ModelIncompleteError("simulated incomplete response")
        return "fail_closed_incomplete" if not error.retryable else "unexpected_retryable"
    raise KeyError(f"Unknown model protocol scenario: {scenario}")
