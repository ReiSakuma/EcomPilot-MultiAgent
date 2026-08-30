from __future__ import annotations

import json
from typing import Any

from app.agents.base import Agent
from app.context.manager import ContextManager
from app.context.strategy import build_strategy_stage_context
from app.context.token_budget import estimate_tokens
from app.memory.long_term import LongTermMemory
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.contracts import (
    PROMOTION_PROTOCOL_VERSION,
    CoreStrategyProposal,
    FixedAmountCouponSpec,
    NoPromotionSpec,
)
from app.model.policy import LlmPolicy
from app.model.structured import parse_json_object
from app.model.telemetry import completed_model_record, failed_model_record
from app.orchestration.failures import failure_from_exception
from app.orchestration.handoff import Handoff
from app.orchestration.react_loop import BoundedReactLoop, ReactToolConstraintError
from app.orchestration.state import TaskState
from app.safety.content_revision import findings_for_agent, scrub_strategy_result
from app.safety.permissions import RiskLevel
from app.safety.policy_gateway import AgentPrincipal, PolicyBudget, PolicyContext
from app.safety.strategy_rendering import render_authoritative_strategy
from app.tools.pricing_tools import maximum_safe_discount
from app.tools.registry import ToolRegistry


class StrategyAgent(Agent):
    """Single-proposal Strategy Agent for the interview core."""

    name = "strategy_agent"
    optional_evidence_tools = frozenset(
        {
            "forecast_demand",
            "query_campaign_history",
            "analyze_competitor_price_trends",
        }
    )

    def __init__(
        self,
        tools: ToolRegistry,
        context_manager: ContextManager | None = None,
        long_term_memory: LongTermMemory | None = None,
        model_adapter: ModelAdapter | None = None,
        llm_policy: LlmPolicy | None = None,
        react_loop: BoundedReactLoop | None = None,
    ) -> None:
        super().__init__(
            tools, context_manager, long_term_memory, model_adapter, llm_policy
        )
        self.react_loop = react_loop

    def run(self, state: TaskState) -> Handoff:
        context = self.build_context(state, token_budget=600)
        stage = build_strategy_stage_context(state)
        stage_payload = stage.model_dump(mode="json")
        stage_text = json.dumps(
            stage_payload, ensure_ascii=False, separators=(",", ":")
        )
        state.context_usage[f"{self.name}:stage"] = {
            "context_policy_version": "interview-core-v1",
            "source_context_tokens": context.token_estimate,
            "stage_context_tokens": estimate_tokens(stage_text),
            "deduplicated": True,
        }

        loop = state.workflow_loops.get("compliance_repair") or state.workflow_loops.get(
            "listing_review"
        )
        if loop and loop.phase == "revision_pending" and self.name in loop.target_agents:
            return self._run_single_revision(state, loop)

        price = float(state.constraints.get("target_price", 0))
        cost = float(state.constraints.get("cost", 0))
        min_margin = float(state.constraints.get("min_margin_rate", 0))
        inventory = int(state.constraints.get("inventory", 0))
        planned_units = int(state.constraints.get("planned_units", 300))
        with self.tools.agent_scope(
            self.name,
            task_id=state.task_id,
            tenant_id=state.principal.tenant_id,
        ):
            suggestion = self.tools.call(
                "suggest_discount",
                price=price,
                cost=cost,
                min_margin_rate=min_margin,
            )
        suggested_ceiling = float(suggestion["discount_amount_yuan"])
        arithmetic_ceiling = maximum_safe_discount(price, cost, min_margin)
        discount_ceiling = max(0.0, min(suggested_ceiling, arithmetic_ceiling))

        proposal: CoreStrategyProposal | None = None
        evidence: dict[str, Any] = {}
        context_budget: dict[str, Any] = {}
        mode = "deterministic"
        try:
            if self.llm_enabled() and self.llm_policy.react_enabled_for(self.name):
                proposal, evidence, context_budget = self._run_core_react(
                    state,
                    stage_payload,
                    discount_ceiling=discount_ceiling,
                )
                mode = "react"
            elif self.llm_enabled():
                proposal = self._run_single_generation(
                    state,
                    stage_payload,
                    discount_ceiling=discount_ceiling,
                )
                mode = "llm"
        except Exception as exc:
            state.degradations.append(
                failure_from_exception(
                    exc,
                    stage="strategy_optional_reasoning",
                    agent_name=self.name,
                    trace_refs=(state.run_id,),
                )
            )
            state.model_fallbacks.append(
                {
                    "agent_name": self.name,
                    "purpose": "strategy_core",
                    "provider": self.model_adapter.provider,
                    "model": self.model_adapter.model,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "fallback": "deterministic_verified_strategy",
                }
            )
            mode = "deterministic_fallback"

        if proposal is None:
            proposal = CoreStrategyProposal(
                launch_plan="围绕目标人群进行首月小规模冷启动，观察转化后再调整投放。",
                rationale="模型未参与或未完成，使用受信业务工具生成可执行基础策略",
                discount_amount_yuan=discount_ceiling,
            )

        requested_discount = float(proposal.discount_amount_yuan)
        verified_discount = round(
            min(max(requested_discount, 0.0), discount_ceiling), 2
        )
        with self.tools.agent_scope(
            self.name,
            task_id=state.task_id,
            tenant_id=state.principal.tenant_id,
        ):
            margin = self.tools.call(
                "calculate_margin",
                price=price,
                cost=cost,
                discount_amount_yuan=verified_discount,
            )
            inventory_check = self.tools.call(
                "check_inventory",
                inventory=inventory,
                planned_units=planned_units,
            )
        promotion = (
            NoPromotionSpec()
            if verified_discount == 0
            else FixedAmountCouponSpec(discount_amount_yuan=verified_discount)
        )
        corrections: list[dict[str, Any]] = []
        if requested_discount != verified_discount:
            corrections.append(
                {
                    "correction_id": f"strategy_discount_cap_{state.task_id}",
                    "source_agent": self.name,
                    "field_path": "strategy.coupon",
                    "issue_code": "discount_above_verified_ceiling",
                    "before": requested_discount,
                    "after": verified_discount,
                    "reason": "模型优惠建议超过确定性毛利与首发政策上限，已按工具结果收敛",
                    "evidence_refs": ["tool.suggest_discount", "tool.calculate_margin"],
                    "method": "interview_core_numeric_owner",
                    "status": "corrected",
                }
            )

        market = state.agent_outputs.get("market_agent", {})
        result = {
            "price": price,
            "promotion_protocol_version": PROMOTION_PROTOCOL_VERSION,
            "promotion": promotion.model_dump(mode="json"),
            "coupon": verified_discount,
            "launch_plan": proposal.launch_plan,
            "planned_units": planned_units,
            "margin": margin,
            "inventory_check": inventory_check,
            "strategy_rationale": proposal.rationale,
            "generation_mode": mode,
            "market_price_reference": {
                "price_band": market.get("price_band"),
                "median_price": market.get("median_price"),
            },
            "selected_evidence_tools": list(evidence),
            "decision_evidence": evidence,
            "proposal_audit": {
                "requested_discount_amount_yuan": requested_discount,
                "verified_discount_ceiling_yuan": discount_ceiling,
                "final_discount_amount_yuan": verified_discount,
            },
            "react_context_budget": context_budget,
            "semantic_corrections": corrections,
            "core_protocol_version": "interview-core-strategy-v1",
        }
        render_authoritative_strategy(
            result,
            state.constraints,
            category=str(state.constraints.get("category") or "商品"),
        )
        return Handoff(
            task_id=state.task_id,
            source_agent=self.name,
            target_agent="review_agent",
            result=result,
            confidence=0.88,
        )

    def _run_core_react(
        self,
        state: TaskState,
        stage_payload: dict[str, Any],
        *,
        discount_ceiling: float,
    ) -> tuple[CoreStrategyProposal, dict[str, Any], dict[str, Any]]:
        if self.react_loop is None:
            raise RuntimeError("Strategy ReAct loop is not configured")
        policy = PolicyContext(
            principal=AgentPrincipal(
                agent_name=self.name,
                task_id=state.task_id,
                tenant_id=state.principal.tenant_id,
            ),
            max_risk_level=RiskLevel.low,
            tool_allowlist=self.optional_evidence_tools,
            budget=PolicyBudget(max_total_calls=1, max_side_effect_calls=0),
        )
        schema = CoreStrategyProposal.model_json_schema()
        system_prompt = (
            "You are EcomPilot Strategy Agent. Produce one concise launch strategy. "
            "You may call zero or one optional read-only evidence tool when it materially "
            "changes the decision; otherwise answer directly. Never call multiple tools. "
            "The datasets are synthetic interview evidence, not live platform data. "
            "Use only confirmed product facts. The program owns margin and inventory math. "
            f"Choose discount_amount_yuan between 0 and {discount_ceiling:g}. "
            "Return only one JSON object matching this schema after any tool result:\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
        user_prompt = json.dumps(
            {"trusted_context": stage_payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )

        def record_response(response: ModelResponse, step: int) -> None:
            state.model_records.append(
                completed_model_record(
                    response,
                    agent_name=self.name,
                    purpose=f"strategy_core_react_step_{step}",
                    structured_validation=(
                        "tool_calls_validated" if response.tool_calls else "pending"
                    ),
                )
            )

        def record_error(exc: Exception, step: int) -> None:
            state.model_records.append(
                failed_model_record(
                    self.model_adapter,
                    exc,
                    agent_name=self.name,
                    purpose=f"strategy_core_react_step_{step}",
                )
            )

        def validate_batch(calls, previous_results: dict) -> None:
            if len(calls) > 1 or previous_results:
                raise ReactToolConstraintError(
                    "Interview-core Strategy permits at most one optional evidence tool"
                )

        outcome = self.react_loop.run(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            policy_context=policy,
            required_tools=set(),
            tool_descriptions={
                "forecast_demand": "Estimate a bounded demand range when launch quantity is uncertain.",
                "query_campaign_history": "Read comparable synthetic campaign outcomes.",
                "analyze_competitor_price_trends": "Read recent synthetic category price movement.",
            },
            validate_tool_batch=validate_batch,
            project_tool_result=self._project_evidence,
            max_tool_error_recoveries=0,
            force_final_after_tool_calls=1,
            on_model_response=record_response,
            on_model_error=record_error,
            input_token_budget=5000,
            max_output_tokens=900,
        )
        proposal = CoreStrategyProposal.model_validate(
            parse_json_object(outcome.final_text)
        )
        self._mark_structured_valid(state, outcome.final_call_id)
        evidence = {
            name: value
            for name, value in outcome.tool_results.items()
            if name in self.optional_evidence_tools
        }
        return proposal, evidence, outcome.context_budget_summary()

    def _run_single_generation(
        self,
        state: TaskState,
        stage_payload: dict[str, Any],
        *,
        discount_ceiling: float,
    ) -> CoreStrategyProposal:
        schema = CoreStrategyProposal.model_json_schema()
        prompt = (
            "Generate one concise ecommerce launch strategy from trusted context. "
            "Use only confirmed facts. Do not calculate margin or inventory. "
            f"discount_amount_yuan must be between 0 and {discount_ceiling:g}. "
            "Return JSON only.\n"
            + json.dumps(stage_payload, ensure_ascii=False, separators=(",", ":"))
        )
        response = self._call_model(
            state, prompt, schema, "strategy_core_single", max_output_tokens=900
        )
        proposal = CoreStrategyProposal.model_validate(parse_json_object(response.text))
        self._mark_structured_valid(state, response.call_id)
        return proposal

    def _run_single_revision(self, state: TaskState, loop) -> Handoff:
        result = dict(
            state.require_agent_output(
                self.name,
                required_keys=(
                    "price",
                    "coupon",
                    "planned_units",
                    "margin",
                    "inventory_check",
                ),
            )
        )
        feedback = findings_for_agent(list(loop.feedback), self.name)
        scrub_strategy_result(
            result,
            feedback,
            category=str(state.constraints.get("category") or "商品"),
        )
        result["generation_mode"] = "safe_revision"
        result["revision_iteration"] = loop.iteration
        result["revision_applied_findings"] = feedback
        render_authoritative_strategy(
            result,
            state.constraints,
            category=str(state.constraints.get("category") or "商品"),
        )
        return Handoff(
            task_id=state.task_id,
            source_agent=self.name,
            target_agent="review_agent",
            result=result,
            confidence=0.9,
        )

    @staticmethod
    def _project_evidence(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        return {
            key: result.get(key)
            for key in (
                "status",
                "summary",
                "forecast_units",
                "range",
                "horizon_days",
                "source_type",
                "evidence_refs",
            )
            if result.get(key) is not None
        }
