from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.reliability.classifier import build_error_signature, classify_failure
from app.reliability.models import FailureTaxonomy


class TaskOutcome(str, Enum):
    """Stable business-facing outcome independent from internal workflow states."""

    created = "created"
    running = "running"
    awaiting_approval = "awaiting_approval"
    waiting_for_input = "waiting_for_input"
    completed = "completed"
    business_rejected = "business_rejected"
    technical_failed = "technical_failed"
    needs_attention = "needs_attention"


class FailureEnvelope(BaseModel):
    """One versioned failure contract shared by orchestration, APIs, and UIs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0", "1.1"] = "1.1"
    code: str = Field(min_length=1, max_length=100)
    category: Literal[
        "business_rule",
        "model_protocol",
        "model_service",
        "tool_policy",
        "tool_execution",
        "timeout",
        "workflow_contract",
        "security",
        "unknown",
    ]
    stage: str = Field(min_length=1, max_length=80)
    agent_name: str | None = None
    user_message: str = Field(min_length=1, max_length=500)
    developer_message: str = Field(min_length=1, max_length=2_000)
    recoverable: bool = False
    retry_action: Literal[
        "regenerate", "retry_stage", "adjust_input", "contact_support", "none"
    ] = "none"
    trace_refs: tuple[str, ...] = ()
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    taxonomy_category: FailureTaxonomy = FailureTaxonomy.unknown
    error_signature: str = ""
    retryable: bool = False
    retry_attempt: int = Field(default=0, ge=0)
    retry_budget_remaining: int | None = Field(default=None, ge=0)
    transport_attempts: int = Field(default=0, ge=0)


class AgentOutputContractError(RuntimeError):
    safe_to_retry = False


def failure_from_exception(
    exc: Exception,
    *,
    stage: str,
    agent_name: str | None = None,
    trace_refs: tuple[str, ...] = (),
) -> FailureEnvelope:
    """Classify implementation exceptions without importing every subsystem."""

    name = type(exc).__name__
    message = str(exc) or name
    normalized = f"{name} {message}".lower()

    if "authentication" in normalized or "401" in normalized or "403" in normalized:
        return _failure(
            "model_authentication_failed",
            "model_service",
            "模型服务身份验证失败，请检查 API Key 和模型权限。",
            message,
            stage,
            agent_name,
            False,
            "contact_support",
            trace_refs,
        )
    if "ratelimit" in normalized or "rate limit" in normalized or "429" in normalized:
        return _failure(
            "model_rate_limited",
            "model_service",
            "模型服务当前请求过多，请稍后重新生成。",
            message,
            stage,
            agent_name,
            True,
            "regenerate",
            trace_refs,
        )
    if any(
        token in normalized
        for token in (
            "modeltransienterror",
            "transport failed",
            "remote end closed",
            "remotedisconnected",
            "connection reset",
            "connection refused",
            "bad record mac",
            "ssl",
            "tls",
        )
    ):
        attempts = int(getattr(exc, "model_request_attempts", 1) or 1)
        return _failure(
            "model_network_unavailable",
            "model_service",
            (
                "模型服务网络连接暂时中断，"
                f"系统已自动尝试 {attempts} 次，仍未获得完整响应。"
                "本次没有修改店铺，已保留完成的上游结果，可以稍后重试当前环节。"
            ),
            message,
            stage,
            agent_name,
            True,
            "retry_stage",
            trace_refs,
            transport_attempts=attempts,
        )
    if "timeout" in normalized or "timed out" in normalized:
        return _failure(
            "execution_timeout",
            "timeout",
            "该环节超过了系统允许的处理时间，请稍后重新生成。",
            message,
            stage,
            agent_name,
            True,
            "regenerate",
            trace_refs,
        )
    if any(
        token in normalized
        for token in (
            "reacttoolconstrainterror",
            "reactlooplimiterror",
            "reactrepeatedactionerror",
            "structuredoutputerror",
            "toolcallingprotocolerror",
            "validationerror",
            "not valid json",
            "react step",
        )
    ):
        return _failure(
            "model_protocol_mismatch",
            "model_protocol",
            "模型返回的工具调用或结构化结果不符合当前协议。",
            message,
            stage,
            agent_name,
            True,
            "regenerate",
            trace_refs,
        )
    if any(token in normalized for token in ("sqlpolicy", "policydenied", "permission")):
        return _failure(
            "tool_policy_denied",
            "tool_policy",
            "工具请求没有通过只读、租户或权限检查。",
            message,
            stage,
            agent_name,
            True,
            "retry_stage",
            trace_refs,
        )
    if any(token in normalized for token in ("capability", "a2a", "security")):
        return _failure(
            "security_contract_failed",
            "security",
            "智能体之间的授权或协作校验未通过。",
            message,
            stage,
            agent_name,
            False,
            "contact_support",
            trace_refs,
        )
    if any(
        token in normalized
        for token in (
            "contract",
            "stateversion",
            "artifact",
            "missing_revision_target",
            "repeated_ready_node_set",
        )
    ):
        return _failure(
            "workflow_contract_failed",
            "workflow_contract",
            "系统内部的数据接口没有通过一致性检查。",
            message,
            stage,
            agent_name,
            False,
            "contact_support",
            trace_refs,
        )
    if any(token in normalized for token in ("tool", "sql", "sandbox", "execution")):
        return _failure(
            "tool_execution_failed",
            "tool_execution",
            "外部工具未能返回符合要求的结果。",
            message,
            stage,
            agent_name,
            True,
            "retry_stage",
            trace_refs,
        )
    if "model" in normalized or "provider" in normalized or "incomplete" in normalized:
        return _failure(
            "model_service_failed",
            "model_service",
            "模型服务没有返回完整可用的结果。",
            message,
            stage,
            agent_name,
            True,
            "regenerate",
            trace_refs,
        )
    return _failure(
        "unclassified_technical_failure",
        "unknown",
        "系统在该环节遇到未分类的技术问题。",
        message,
        stage,
        agent_name,
        False,
        "contact_support",
        trace_refs,
    )


def business_failure(
    *,
    code: str,
    stage: str,
    user_message: str,
    developer_message: str,
    agent_name: str = "review_agent",
    retry_action: Literal["adjust_input", "regenerate", "none"] = "adjust_input",
) -> FailureEnvelope:
    return _failure(
        code,
        "business_rule",
        user_message,
        developer_message,
        stage,
        agent_name,
        retry_action != "none",
        retry_action,
        (),
    )


def _failure(
    code: str,
    category: str,
    user_message: str,
    developer_message: str,
    stage: str,
    agent_name: str | None,
    recoverable: bool,
    retry_action: str,
    trace_refs: tuple[str, ...],
    *,
    transport_attempts: int = 0,
) -> FailureEnvelope:
    synthetic = RuntimeError(developer_message)
    taxonomy = classify_failure(synthetic)
    category_overrides = {
        "business_rule": FailureTaxonomy.business_rule,
        "security": FailureTaxonomy.permission_denied,
        "tool_policy": FailureTaxonomy.permission_denied,
        "model_protocol": FailureTaxonomy.schema_invalid,
        "workflow_contract": FailureTaxonomy.schema_invalid,
        "timeout": FailureTaxonomy.transient,
    }
    taxonomy = category_overrides.get(category, taxonomy)
    return FailureEnvelope(
        code=code,
        category=category,
        stage=stage,
        agent_name=agent_name,
        user_message=user_message,
        developer_message=developer_message,
        recoverable=recoverable,
        retry_action=retry_action,
        trace_refs=trace_refs,
        taxonomy_category=taxonomy,
        error_signature=build_error_signature(
            synthetic,
            agent_name=agent_name,
            code=code,
        ),
        retryable=recoverable and taxonomy not in {
            FailureTaxonomy.business_rule,
            FailureTaxonomy.permission_denied,
            FailureTaxonomy.permanent,
        },
        transport_attempts=transport_attempts,
    )
