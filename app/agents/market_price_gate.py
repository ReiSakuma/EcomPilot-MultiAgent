from __future__ import annotations

from app.agents.base import Agent
from app.orchestration.handoff import Handoff
from app.orchestration.state import TaskState
from app.tools.market_price_gate import (
    MarketPriceAssessmentInput,
    assess_market_price_position,
)


class MarketPriceGateAgent(Agent):
    """A deterministic business gate; it never calls an LLM or a tool."""

    name = "market_price_gate_agent"

    def run(self, state: TaskState) -> Handoff:
        market = state.require_agent_output(
            "market_agent", required_keys=("market_layers", "sample_size")
        )
        layers = market["market_layers"]
        core = layers["core_comparable"]["price_distribution"]
        assessment = assess_market_price_position(
            MarketPriceAssessmentInput(
                target_price=float(state.constraints["target_price"]),
                cost=float(state.constraints["cost"]),
                min_margin_rate=float(state.constraints.get("min_margin_rate", 0)),
                category=str(state.constraints.get("category") or "未知品类"),
                pricing_profile=str(
                    state.constraints.get("pricing_profile") or "standard"
                ),
                core_reference_price=layers.get("core_reference_price"),
                reference_method=str(layers.get("reference_method") or "unavailable"),
                core_mean_price=core.get("mean"),
                core_median_price=core.get("median"),
                core_price_band=(core["minimum"], core["maximum"])
                if core.get("minimum") is not None and core.get("maximum") is not None
                else None,
                full_market_band=tuple(market.get("full_market_band") or ()) or None,
                core_sample_count=int(layers["core_comparable"]["sample_count"]),
                excluded_sample_count=int(
                    market["sample_size"].get("excluded_competitors", 0)
                ),
                market_mode=layers["mode"],
                distribution_status=layers["distribution_status"],
                pricing_override=bool(state.constraints.get("pricing_override")),
                pricing_override_evidence=tuple(
                    state.constraints.get("pricing_override_evidence") or ()
                ),
            )
        )
        result = assessment.model_dump(mode="json")
        return Handoff(
            task_id=state.task_id,
            source_agent=self.name,
            target_agent="listing_agent",
            status=(
                "requires_input"
                if assessment.status == "confirmation_required"
                else "completed"
            ),
            result=result,
            confidence=1.0,
            evidence_refs=[state.latest_artifacts["market_agent"]],
            error=(
                "price_confirmation_required"
                if assessment.status == "confirmation_required"
                else None
            ),
        )
