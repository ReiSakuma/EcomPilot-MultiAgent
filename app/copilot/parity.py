from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.access.models import AccessPrincipal
from app.agents.supervisor import Supervisor
from app.copilot.facade import ConversationFacade
from app.copilot.graph import V28ConversationGraph
from app.orchestration.state import TaskState


class GraphParityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    graph_steps: list[str]
    legacy_projection: dict[str, Any]
    graph_projection: dict[str, Any]
    differences: list[str]


def run_graph_parity(
    message: str,
    *,
    principal: AccessPrincipal,
    legacy_supervisor_factory: Callable[[], Supervisor] = Supervisor,
    graph_supervisor_factory: Callable[[], Supervisor] = Supervisor,
) -> GraphParityReport:
    """Run both paths for deterministic fixtures and compare business projections."""

    legacy_state = legacy_supervisor_factory().run(
        message,
        approved=False,
        principal=principal,
    )
    graph_response, graph_steps = V28ConversationGraph(
        graph_supervisor_factory
    ).invoke(
        message,
        principal=principal,
        conversation_id="conv_parity_fixture",
        turn_id="turn_parity_fixture",
    )
    legacy_response = ConversationFacade.build_response(legacy_state)
    legacy_projection = _projection(legacy_response)
    graph_projection = _projection(graph_response)
    differences = [
        key
        for key in legacy_projection
        if legacy_projection.get(key) != graph_projection.get(key)
    ]
    return GraphParityReport(
        passed=not differences
        and graph_steps == ["receive", "legacy_listing_workflow", "answer"],
        graph_steps=graph_steps,
        legacy_projection=legacy_projection,
        graph_projection=graph_projection,
        differences=differences,
    )


def _projection(response) -> dict[str, Any]:
    panels = {panel.panel_id: panel for panel in response.panels}
    strategy = panels["strategy"].data
    review = panels["review"].data
    return {
        "outcome": response.outcome.value,
        "requirements": response.understood_requirements,
        "market": panels["market"].data,
        "listing": panels["listing"].data,
        "strategy_price": strategy.get("price"),
        "strategy_coupon": strategy.get("coupon"),
        "strategy_margin": strategy.get("margin"),
        "strategy_inventory": strategy.get("inventory_check"),
        "review_approved": review.get("approved_for_execution"),
        "review_violations": review.get("violations", []),
        "approval_required": response.approval_required,
        "store_modified": response.store_modified,
    }
