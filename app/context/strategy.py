from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.orchestration.state import TaskState


class StrategyTaskIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    checkpoint_version: int = Field(ge=0)


class StrategyTrustedConstraints(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    target_audience: str | None = None
    price: float
    cost: float
    inventory: int
    minimum_margin_rate: float
    planned_units: int
    operation_goal: str | None = None


class StrategyConfirmedProductFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    features: tuple[str, ...] = ()
    product_form: str | None = None


class StrategyMarketDigest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    core_reference_price: float | None = None
    reference_method: str | None = None
    core_price_band: tuple[float, float] | None = None
    full_market_band: tuple[float, float] | None = None
    acceptance_band: tuple[float, float] | None = None
    core_sample_count: int = 0
    evidence_quality: str | None = None
    highlights: tuple[str, ...] = ()
    pain_points: tuple[str, ...] = ()


class StrategyStageContext(BaseModel):
    """Minimal trusted projection shared by Strategy's internal v60 stages."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    task_identity: StrategyTaskIdentity
    trusted_constraints: StrategyTrustedConstraints
    confirmed_product_facts: StrategyConfirmedProductFacts
    market_digest: StrategyMarketDigest


def build_strategy_stage_context(state: TaskState) -> StrategyStageContext:
    constraints = state.constraints
    market = state.agent_outputs.get("market_agent", {})
    gate = state.agent_outputs.get("market_price_gate_agent", {})
    layers = market.get("market_layers") or {}
    core = layers.get("core_comparable") or {}
    price_distribution = core.get("price_distribution") or {}

    return StrategyStageContext(
        task_identity=StrategyTaskIdentity(
            task_id=state.task_id,
            checkpoint_version=state.checkpoint_version,
        ),
        trusted_constraints=StrategyTrustedConstraints(
            category=str(constraints.get("category") or "商品"),
            target_audience=_optional_text(constraints.get("target_audience")),
            price=float(constraints.get("target_price", 199)),
            cost=float(constraints.get("cost", 95)),
            inventory=int(constraints.get("inventory", 0)),
            minimum_margin_rate=float(constraints.get("min_margin_rate", 0.25)),
            planned_units=int(constraints.get("planned_units", 300)),
            operation_goal=_operation_goal(state.goal),
        ),
        confirmed_product_facts=StrategyConfirmedProductFacts(
            features=tuple(
                str(item)
                for item in constraints.get("confirmed_features", [])
                if str(item).strip()
            ),
            product_form=_optional_text(constraints.get("confirmed_product_form")),
        ),
        market_digest=StrategyMarketDigest(
            core_reference_price=_optional_float(
                layers.get("core_reference_price", market.get("core_reference_price"))
            ),
            reference_method=_optional_text(
                layers.get("reference_method", market.get("reference_method"))
            ),
            core_price_band=_price_band(
                (
                    price_distribution.get("minimum"),
                    price_distribution.get("maximum"),
                )
                if price_distribution
                else market.get("price_band")
            ),
            full_market_band=_price_band(market.get("full_market_band")),
            acceptance_band=_price_band(gate.get("acceptance_band")),
            core_sample_count=int(core.get("sample_count") or 0),
            evidence_quality=_optional_text(gate.get("evidence_quality")),
            highlights=_bounded_texts(market.get("highlights"), limit=5),
            pain_points=_bounded_texts(market.get("pain_points"), limit=5),
        ),
    )


def _operation_goal(goal: str) -> str | None:
    match = re.search(r"运营目标\s*[：:]\s*(.+?)(?:。|$)", goal)
    return _optional_text(match.group(1)) if match else None


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _price_band(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if value[0] is None or value[1] is None:
        return None
    return float(value[0]), float(value[1])


def _bounded_texts(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item)[:160] for item in value[:limit] if str(item).strip())
