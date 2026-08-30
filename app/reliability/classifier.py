from __future__ import annotations

import hashlib
import re

from pydantic import ValidationError

from app.reliability.models import FailureTaxonomy
from app.tools.schemas import (
    ToolParameterError,
    ToolResultValidationError,
    ToolTimeoutError,
    TransientToolError,
    UnknownWriteStateError,
)


_VOLATILE = re.compile(
    r"(?:[0-9a-f]{8}-[0-9a-f-]{27,}|\b(?:task|run|tool)_[0-9a-f]+\b|"
    r"\b\d{4}-\d{2}-\d{2}[tT ][0-9:.+zZ-]+\b|\b\d{4,}\b)",
    re.IGNORECASE,
)
_SPACE = re.compile(r"\s+")


def classify_failure(exc: BaseException) -> FailureTaxonomy:
    message = f"{type(exc).__name__} {exc}".lower()
    if isinstance(exc, UnknownWriteStateError):
        return FailureTaxonomy.unknown
    if any(token in message for token in ("429", "rate limit", "ratelimit", "retry-after")):
        return FailureTaxonomy.rate_limit
    if any(token in message for token in ("401", "403", "permission", "forbidden", "capability")):
        return FailureTaxonomy.permission_denied
    if any(token in message for token in ("409", "conflict", "stale", "version mismatch", "locked")):
        return FailureTaxonomy.concurrency_conflict
    if any(token in message for token in ("business rule", "margin_below", "inventory_below")):
        return FailureTaxonomy.business_rule
    if any(token in message for token in ("400", "404", "not found", "unsupported", "permanent")):
        return FailureTaxonomy.permanent
    if isinstance(exc, (ValidationError, ToolParameterError, ToolResultValidationError)):
        return FailureTaxonomy.schema_invalid
    if isinstance(exc, (ToolTimeoutError, TransientToolError, TimeoutError, ConnectionError, OSError)):
        return FailureTaxonomy.transient
    if any(
        token in message
        for token in (
            "502",
            "503",
            "504",
            "temporar",
            "modeltransienterror",
            "modelincompleteerror",
            "transport failed",
            "connection reset",
            "connection refused",
            "remote end closed",
            "remotedisconnected",
            "bad record mac",
            "ssl",
            "tls",
            "finish_reason=length",
            "response incomplete",
        )
    ):
        return FailureTaxonomy.transient
    if any(token in message for token in ("schema", "json", "validation", "structured output")):
        return FailureTaxonomy.schema_invalid
    return FailureTaxonomy.unknown


def normalize_error_message(message: str) -> str:
    normalized = _VOLATILE.sub("<volatile>", message.strip().lower())
    return _SPACE.sub(" ", normalized)[:500]


def build_error_signature(
    exc: BaseException,
    *,
    agent_name: str | None = None,
    tool_name: str | None = None,
    code: str | None = None,
) -> str:
    material = "|".join(
        (
            agent_name or "-",
            tool_name or "-",
            code or type(exc).__name__,
            normalize_error_message(str(exc) or type(exc).__name__),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
