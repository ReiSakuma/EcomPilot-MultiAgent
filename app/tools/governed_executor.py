from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.model.tool_calling import ModelToolCall, ToolDefinition
from app.safety.permissions import RiskLevel
from app.safety.policy_gateway import (
    PolicyContext,
    PolicyDecision,
    ToolPolicyGateway,
)
from app.tools.registry import ToolRegistry


class GovernedToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    tool_name: str
    result: Any
    policy_decision: PolicyDecision


class GovernedToolExecutor:
    """The only V18 entry point for executing model-selected tool calls."""

    def __init__(
        self, registry: ToolRegistry, policy_gateway: ToolPolicyGateway
    ) -> None:
        self.registry = registry
        self.policy_gateway = policy_gateway

    def definitions_for(
        self,
        context: PolicyContext,
        descriptions: dict[str, str] | None = None,
    ) -> list[ToolDefinition]:
        descriptions = descriptions or {}
        definitions: list[ToolDefinition] = []
        for name, spec in sorted(self.registry.specs().items()):
            if spec.allowed_agents and context.principal.agent_name not in spec.allowed_agents:
                continue
            if context.tool_allowlist and name not in context.tool_allowlist:
                continue
            if _risk_rank(spec.risk_level) > _risk_rank(context.max_risk_level):
                continue
            definitions.append(
                ToolDefinition(
                    name=name,
                    description=descriptions.get(
                        name, f"EcomPilot governed tool: {name.replace('_', ' ')}"
                    ),
                    input_model=self.registry.input_model(name),
                )
            )
        return definitions

    def execute(
        self, call: ModelToolCall, context: PolicyContext
    ) -> GovernedToolResult:
        return self.execute_many([call], context)[0]

    def execute_many(
        self, calls: list[ModelToolCall], context: PolicyContext
    ) -> list[GovernedToolResult]:
        decisions = self.policy_gateway.authorize_many(
            calls, context, self.registry.specs()
        )
        results: list[GovernedToolResult] = []
        approved = context.approval is not None
        approved_by = context.approval.approved_by if context.approval else None
        with self.registry.agent_scope(
            context.principal.agent_name,
            approved=approved,
            approved_by=approved_by,
            task_id=context.principal.task_id,
        ):
            for call, decision in zip(calls, decisions, strict=True):
                result = self.registry.call(call.name, **call.arguments)
                results.append(
                    GovernedToolResult(
                        call_id=call.call_id,
                        tool_name=call.name,
                        result=result,
                        policy_decision=decision,
                    )
                )
        return results


def _risk_rank(level: RiskLevel) -> int:
    return {
        RiskLevel.low: 0,
        RiskLevel.medium: 1,
        RiskLevel.high: 2,
    }[level]
