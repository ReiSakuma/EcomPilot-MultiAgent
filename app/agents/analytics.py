from __future__ import annotations

import json
import re
from typing import Any

from app.agents.base import Agent
from app.context.manager import ContextManager
from app.memory.long_term import LongTermMemory
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.telemetry import completed_model_record
from app.model.contracts import AnalyticsModelOutput
from app.model.policy import LlmPolicy
from app.orchestration.handoff import Handoff
from app.orchestration.react_loop import BoundedReactLoop, ReactLoopError
from app.orchestration.state import TaskState
from app.safety.permissions import RiskLevel
from app.safety.policy_gateway import AgentPrincipal, PolicyBudget, PolicyContext
from app.tools.registry import ToolRegistry


ANALYTICS_TOOLS = {
    "get_sales_metrics",
    "compare_sales_periods",
    "get_campaign_performance",
    "get_inventory_history",
}


class AnalyticsAgent(Agent):
    """Read-only product analytics with bounded, model-selected evidence gathering."""

    name = "analytics_agent"

    def __init__(
        self,
        tools: ToolRegistry,
        context_manager: ContextManager | None = None,
        long_term_memory: LongTermMemory | None = None,
        model_adapter: ModelAdapter | None = None,
        llm_policy: LlmPolicy | None = None,
        react_loop: BoundedReactLoop | None = None,
    ) -> None:
        super().__init__(tools, context_manager, long_term_memory, model_adapter, llm_policy)
        self.react_loop = react_loop

    def run(self, state: TaskState) -> Handoff:
        self.build_context(state, token_budget=700)
        if self.llm_policy.react_enabled_for(self.name) and self.llm_enabled():
            try:
                results, selected, context_budget = self._react_evidence(state)
                return self._handoff(
                    state,
                    results,
                    selected,
                    "react_read_only",
                    react_context_budget=context_budget,
                )
            except ReactLoopError as exc:
                state.model_fallbacks.append(
                    {
                        "agent_name": self.name,
                        "purpose": "analytics_evidence_selection",
                        "provider": self.model_adapter.provider,
                        "model": self.model_adapter.model,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "fallback": "deterministic_read_only_selection",
                    }
                )
        results, selected = self._deterministic_evidence(state)
        return self._handoff(state, results, selected, self.deterministic_mode(state))

    def _react_evidence(
        self, state: TaskState
    ) -> tuple[dict[str, dict], list[str], dict[str, Any]]:
        if self.react_loop is None:
            raise ReactLoopError("Analytics ReAct loop is not configured")
        product_id, start_date, end_date = self._range_arguments(state)
        policy = PolicyContext(
            principal=AgentPrincipal(
                agent_name=self.name,
                task_id=state.task_id,
                tenant_id=state.principal.tenant_id,
            ),
            max_risk_level=RiskLevel.low,
            tool_allowlist=frozenset(ANALYTICS_TOOLS),
            budget=PolicyBudget(max_total_calls=4, max_side_effect_calls=0),
        )
        schema = AnalyticsModelOutput.model_json_schema()
        common_args = {
            "product_id": product_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        system_prompt = (
            "You are a read-only ecommerce Analytics Agent. First call get_sales_metrics. "
            "Then autonomously choose only evidence needed for the user's question: comparison, "
            "campaign performance, or inventory history. Never call write tools. Use each tool at "
            "most once and stop after sufficient evidence. Business numbers are owned by tools; "
            "your final JSON may explain evidence selection but must not invent metrics. Return one "
            f"JSON object matching this schema: {json.dumps(schema, ensure_ascii=False)}"
        )
        user_prompt = (
            f"Question: {state.goal}\nTrusted entity and period arguments for every tool: "
            f"{json.dumps(common_args, ensure_ascii=False)}"
        )

        def validate_call(call, previous_results: dict[str, Any]) -> None:
            if call.name in previous_results:
                raise ReactLoopError(f"Analytics tool '{call.name}' may be used only once")
            if call.arguments != common_args:
                raise ReactLoopError("Analytics tool arguments must match the resolved entity and period")

        def record(response: ModelResponse, step: int) -> None:
            state.model_records.append(
                completed_model_record(
                    response,
                    agent_name=self.name,
                    purpose=f"analytics_react_step_{step}",
                    structured_validation=(
                        "tool_calls_validated" if response.tool_calls else "pending"
                    ),
                )
            )

        outcome = self.react_loop.run(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            policy_context=policy,
            required_tools={"get_sales_metrics"},
            tool_descriptions={
                "get_sales_metrics": "Read authoritative sales, revenue, conversion and ending inventory for the resolved period.",
                "compare_sales_periods": "Compare the resolved period with the immediately preceding equal-length period.",
                "get_campaign_performance": "Read promotions overlapping the resolved period and their ROI.",
                "get_inventory_history": "Read stock movements and period opening/ending inventory.",
            },
            validate_tool_call=validate_call,
            force_final_after_tool_calls=3,
            on_model_response=record,
            input_token_budget=min(
                self.react_loop.config.input_token_budget, 9000
            ),
            max_output_tokens=min(
                self.react_loop.config.max_output_tokens, 1400
            ),
        )
        final = self._validate_or_repair_structured(
            state,
            outcome.final_text,
            outcome.final_call_id,
            AnalyticsModelOutput,
            "analytics_final_explanation",
        )
        selected = sorted(outcome.tool_results)
        declared = set(final.selected_evidence_tools)
        if not declared.issubset(set(selected)):
            state.model_fallbacks.append(
                {
                    "agent_name": self.name,
                    "purpose": "analytics_final_explanation",
                    "error_type": "UnexecutedEvidenceReference",
                    "error": "Model cited analytics tools that were not executed",
                    "fallback": "recorded_tool_selection",
                }
            )
        return (
            dict(outcome.tool_results),
            selected,
            outcome.context_budget_summary(),
        )

    def _deterministic_evidence(self, state: TaskState) -> tuple[dict[str, dict], list[str]]:
        product_id, start_date, end_date = self._range_arguments(state)
        arguments = {
            "product_id": product_id,
            "start_date": start_date,
            "end_date": end_date,
        }
        selected = ["get_sales_metrics"]
        text = state.goal
        comparison_mode = str(state.constraints.get("comparison_mode") or "none")
        if comparison_mode == "previous_period" or re.search(r"环比|对比|变化|趋势", text):
            selected.append("compare_sales_periods")
        if comparison_mode == "campaign_window" or re.search(r"活动|促销|投放|ROI", text, re.I):
            selected.append("get_campaign_performance")
        if re.search(r"库存|存货|余量|售罄", text):
            selected.append("get_inventory_history")
        return {name: self.tools.call(name, **arguments) for name in selected}, selected

    @staticmethod
    def _range_arguments(state: TaskState) -> tuple[str, str, str]:
        product_id = str(state.constraints.get("product_id") or "")
        start_date = str(state.constraints.get("start_date") or "")
        end_date = str(state.constraints.get("end_date") or "")
        if not product_id or not start_date or not end_date:
            raise ValueError("Analytics requires a resolved product and an explicit time range")
        return product_id, start_date, end_date

    @staticmethod
    def _handoff(
        state: TaskState,
        results: dict[str, dict],
        selected: list[str],
        generation_mode: str,
        react_context_budget: dict[str, Any] | None = None,
    ) -> Handoff:
        sales = results["get_sales_metrics"]
        metrics = sales["metrics"]
        period_label = str(state.constraints.get("period_label") or "所选期间")
        narrative = (
            f"{period_label}共售出 {metrics['units_sold']} 件，销售额 {metrics['revenue']:.2f} 元，"
            f"转化率 {metrics['conversion_rate']:.2%}，期末库存 {metrics['ending_inventory']} 件。"
        )
        comparison = results.get("compare_sales_periods")
        if comparison:
            change = comparison["change"]
            units_rate = change.get("units_sold_rate")
            narrative += (
                f" 与上一等长周期相比，销量变化 {units_rate:+.2%}。"
                if units_rate is not None
                else " 上一周期销量为零，无法计算销量变化率。"
            )
        evidence_refs = [
            ref
            for result in results.values()
            for ref in result.get("evidence_refs", [])
        ]
        return Handoff(
            task_id=state.task_id,
            source_agent="analytics_agent",
            target_agent="supervisor",
            result={
                "product_id": sales["product_id"],
                "period": {**sales["period"], "label": period_label},
                "sales": sales,
                "comparison": comparison,
                "campaigns": results.get("get_campaign_performance"),
                "inventory": results.get("get_inventory_history"),
                "selected_evidence_tools": selected,
                "narrative": narrative,
                "generation_mode": generation_mode,
                "source_type": sales["source_type"],
                "source_updated_at": sales["source_updated_at"],
                "react_context_budget": react_context_budget,
            },
            confidence=1.0,
            evidence_refs=list(dict.fromkeys(evidence_refs)),
        )
