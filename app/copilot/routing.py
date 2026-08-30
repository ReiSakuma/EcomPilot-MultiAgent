from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from app.copilot.intents import (
    CompiledRequest,
    IntentExecutionGroup,
    IntentName,
    RequestMode,
    RoutePlan,
)


ALL_SPECIALIST_AGENTS = (
    "market_agent",
    "listing_agent",
    "strategy_agent",
    "review_agent",
    "analytics_agent",
    "browser_agent",
)


@dataclass(frozen=True)
class WorkflowTemplate:
    template_id: str
    intents: tuple[IntentName, ...]
    risk_scope: Literal["none", "read", "write_plan", "write_execute"]
    active_components: tuple[str, ...]
    planned_agents: tuple[str, ...]
    capability_scopes: tuple[str, ...]
    approval_required: bool = False
    stop_conditions: tuple[str, ...] = ("terminal_node_reached", "failure_envelope")


class WorkflowTemplateRegistry:
    """Allowlisted workflow templates; models cannot invent arbitrary DAG nodes."""

    def __init__(self) -> None:
        self._templates = {
            "listing_workflow.v1": WorkflowTemplate(
                "listing_workflow.v1",
                (IntentName.create_listing,),
                "write_plan",
                ("preflight_gate", "entity_input", "listing_subgraph", "approval_interrupt", "browser_execution"),
                ("market_agent", "listing_agent", "strategy_agent", "review_agent", "browser_agent"),
                ("market.read", "listing.compose", "strategy.plan", "risk.review", "seller.execute"),
                True,
                ("review_approved", "approval_received", "max_revision_iterations", "terminal_failure"),
            ),
            "modify_listing_workflow.v1": WorkflowTemplate(
                "modify_listing_workflow.v1",
                (IntentName.modify_listing,),
                "write_plan",
                ("entity_resolver", "field_change_plan", "listing_subgraph", "approval_interrupt", "browser_execution"),
                ("market_agent", "listing_agent", "strategy_agent", "review_agent", "browser_agent"),
                ("product.read", "market.read", "listing.compose", "strategy.plan", "risk.review", "seller.execute"),
                True,
                ("entity_resolved", "review_approved", "approval_received", "max_revision_iterations", "terminal_failure"),
            ),
            "market_read_only.v1": WorkflowTemplate(
                "market_read_only.v1",
                (IntentName.market_research,),
                "read",
                ("market_subgraph",),
                ("market_agent",),
                ("market.read", "sql.read"),
            ),
            "product_performance_read_only.v1": WorkflowTemplate(
                "product_performance_read_only.v1",
                (IntentName.product_performance,),
                "read",
                ("entity_resolver", "analytics_subgraph"),
                ("analytics_agent",),
                ("product.read", "analytics.read"),
            ),
            "product_detail_read_only.v1": WorkflowTemplate(
                "product_detail_read_only.v1",
                (IntentName.product_detail,),
                "read",
                ("entity_resolver", "product_ledger"),
                (),
                ("product.read", "task.read"),
            ),
            "task_status_read_only.v1": WorkflowTemplate(
                "task_status_read_only.v1", (IntentName.task_status,), "read",
                ("task_checkpoint",), (), ("task.read",),
            ),
            "memory_candidate.v1": WorkflowTemplate(
                "memory_candidate.v1",
                (IntentName.remember_preference,),
                "write_plan",
                ("memory_candidate", "confirmation_boundary"),
                (),
                ("memory.propose",),
                False,
                ("candidate_saved", "explicit_confirmation_required"),
            ),
            "general_answer.v1": WorkflowTemplate(
                "general_answer.v1",
                (IntentName.general_chat, IntentName.out_of_scope, IntentName.clarify),
                "none",
                ("direct_answer",),
                (),
                (),
            ),
        }

    def select(self, compiled: CompiledRequest) -> WorkflowTemplate:
        if compiled.assessment.mode is RequestMode.advisory:
            return self._templates["general_answer.v1"]
        for template in self._templates.values():
            if compiled.decision.intent in template.intents:
                return template
        raise ValueError(f"No workflow template for {compiled.decision.intent.value}")

    def templates(self) -> tuple[WorkflowTemplate, ...]:
        return tuple(self._templates.values())


class ConversationOrchestrator:
    """Produces a deterministic, auditable route from a validated request."""

    def __init__(self, registry: WorkflowTemplateRegistry | None = None) -> None:
        self.registry = registry or WorkflowTemplateRegistry()

    def plan(self, compiled: CompiledRequest) -> RoutePlan:
        template = self.registry.select(compiled)
        units = list(compiled.intent_units)
        unit_templates = []
        for unit in units:
            probe = compiled.model_copy(deep=True)
            probe.decision.intent = unit.intent
            probe.assessment.mode = (
                RequestMode.read_only if unit.mode == "read_only" else RequestMode.execute
            )
            try:
                unit_templates.append(self.registry.select(probe))
            except ValueError:
                continue
        planned = list(dict.fromkeys(
            name for candidate in ([template] if not unit_templates else unit_templates)
            for name in candidate.planned_agents
        ))
        scopes = list(dict.fromkeys(
            scope for candidate in ([template] if not unit_templates else unit_templates)
            for scope in candidate.capability_scopes
        ))
        groups = _execution_groups(compiled)
        multi = len(units) > 1
        return RoutePlan(
            route_id=f"route_{uuid4().hex[:12]}",
            template_id="multi_intent.v1" if multi else template.template_id,
            intent=compiled.decision.intent,
            risk_scope=_maximum_risk(groups) if groups else template.risk_scope,
            active_components=(
                ["request_compiler", "intent_dag", "artifact_handoff", "response_aggregator"]
                if multi else list(template.active_components)
            ),
            planned_agents=planned,
            skipped_agents=[name for name in ALL_SPECIALIST_AGENTS if name not in planned],
            capability_scopes=scopes,
            approval_required=any(candidate.approval_required for candidate in unit_templates)
            if unit_templates else template.approval_required,
            stop_conditions=list(dict.fromkeys([
                *template.stop_conditions,
                "max_5_intent_units",
                "acyclic_dependencies",
                "conflict_requires_clarification",
            ])),
            intent_units=units,
            execution_groups=groups,
            clarification_required=bool(compiled.conflicts) or any(
                unit.status != "ready" for unit in units
            ),
        )


def _execution_groups(compiled: CompiledRequest) -> list[IntentExecutionGroup]:
    pending = {unit.intent_id: unit for unit in compiled.intent_units if unit.status == "ready"}
    completed: set[str] = set()
    groups: list[IntentExecutionGroup] = []
    while pending:
        ready = [unit for unit in pending.values() if set(unit.dependencies) <= completed]
        if not ready:
            break
        read_units = [unit for unit in ready if unit.mode == "read_only"]
        selected = read_units or [ready[0]]
        risk = "read" if all(unit.mode == "read_only" for unit in selected) else "write_plan"
        groups.append(IntentExecutionGroup(
            group_id=f"group_{len(groups) + 1:02d}",
            intent_ids=[unit.intent_id for unit in selected],
            execution="parallel" if len(selected) > 1 and risk == "read" else "serial",
            risk_scope=risk,
        ))
        for unit in selected:
            completed.add(unit.intent_id)
            pending.pop(unit.intent_id, None)
    return groups


def _maximum_risk(groups: list[IntentExecutionGroup]) -> str:
    order = {"none": 0, "read": 1, "write_plan": 2, "write_execute": 3}
    return max((group.risk_scope for group in groups), key=order.get, default="none")
