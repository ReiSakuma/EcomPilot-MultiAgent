from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.supervisor import Supervisor
from app.config import TRACE_DIR
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.policy import LlmPolicy
from app.model.tool_calling import ModelToolCall
from app.sql.policy import SqlPolicyDeniedError
from app.sql.service import get_market_sql_service
from app.tools.browser_tools import reset_seller_center


GOAL = (
    "我要上架一款成本95元、售价199元、库存800件的无线耳机，"
    "主要面向大学生，毛利率不低于25%。"
)
SQL = (
    "SELECT AVG(price) AS avg_price, MIN(price) AS min_price, "
    "MAX(price) AS max_price FROM products"
)
FINAL = json.dumps(
    {
        "insight_summary": "竞品均价189元，199元位于主流价格带内。",
        "query_rationale": "用价格聚合验证目标售价的市场位置。",
        "recommended_product_ids": [],
    },
    ensure_ascii=False,
)


def response(
    call_id: str,
    *,
    text: str = "",
    tool_calls: list[ModelToolCall] | None = None,
) -> ModelResponse:
    tool_calls = tool_calls or []
    assistant_message = {"role": "assistant", "content": text or None}
    if tool_calls:
        assistant_message["tool_calls"] = [call.to_api() for call in tool_calls]
    return ModelResponse(
        call_id=call_id,
        provider="deepseek",
        model="deepseek-v4-pro-fixture",
        text=text,
        tool_calls=tool_calls,
        assistant_message=assistant_message,
        finish_reason="tool_calls" if tool_calls else "stop",
        input_tokens=20,
        output_tokens=10,
        total_tokens=30,
        usage_source="actual",
        prompt_tokens_estimate=20,
        completion_tokens_estimate=10,
        structured_output_mode="tool_calling",
    )


class AcceptanceModelAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            provider="deepseek",
            model="deepseek-v4-pro-fixture",
            api_key="offline-fixture",
        )
        arguments = {"sql": SQL, "purpose": "student_price_analysis"}
        query_call = ModelToolCall(
            call_id="call_v21_sql",
            name="query_market_database",
            arguments=arguments,
            raw_arguments=json.dumps(arguments),
        )
        self.responses = [
            response("model_v21_sql", tool_calls=[query_call]),
            response("model_v21_final", text=FINAL),
        ]

    def complete_with_tools(self, messages, tools, tool_choice="auto") -> ModelResponse:
        return self.responses.pop(0)


def run_fixture_task():
    reset_seller_center()
    adapter = AcceptanceModelAdapter()
    policy = LlmPolicy(
        enabled_agents={"market_agent"},
        react_enabled_agents={"market_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=2,
        react_max_steps=2,
        react_max_tool_calls=2,
    )
    supervisor = Supervisor()
    supervisor.model_adapter = adapter
    supervisor.llm_policy = policy
    supervisor.react_loop.model_adapter = adapter
    supervisor.react_loop.config = supervisor.react_loop.config.model_copy(
        update={"max_steps": 2, "max_tool_calls": 2}
    )
    market_agent = supervisor.agents["market_agent"]
    market_agent.model_adapter = adapter
    market_agent.llm_policy = policy

    return supervisor.run(GOAL, approved=False)


def main() -> None:
    state = run_fixture_task()
    market = state.agent_outputs["market_agent"]
    sql_research = market["sql_research"]
    artifact = state.artifacts[state.latest_artifacts["market_agent"]]
    trace_path = TRACE_DIR / f"{state.run_id}.jsonl"
    trace_text = trace_path.read_text(encoding="utf-8")
    service = get_market_sql_service()
    try:
        service.query("DELETE FROM products", "acceptance_attack")
    except SqlPolicyDeniedError as denied:
        denied_reason = denied.decision.reason_codes
    else:
        denied_reason = ()

    checks = {
        "model_autonomously_requested_sql_tool": any(
            record.get("agent_name") == "market_agent"
            and record.get("purpose") == "market_text_to_sql_react_step_1"
            and record.get("tool_calls")
            for record in state.model_records
        ),
        "tool_result_returned_before_final_model_call": len(state.model_records) == 2
        and state.model_records[-1]["structured_validation"] == "passed",
        "sql_ast_policy_allowed_select": sql_research["policy"]["status"]
        == "allowed",
        "row_limit_was_injected": sql_research["normalized_sql"].endswith("LIMIT 50")
        and sql_research["policy"]["limit_applied"],
        "aggregate_result_is_correct": sql_research["rows"]
        == [{"avg_price": 379.0035, "min_price": 8.8, "max_price": 12999.0}],
        "sqlite_connection_is_read_only": sql_research["policy"][
            "read_only_connection"
        ]
        is True,
        "dangerous_write_is_denied": "select_only" in denied_reason,
        "market_artifact_contains_sql_evidence": artifact.research_mode
        == "react_text_to_sql"
        and artifact.sql_research is not None
        and any(ref.startswith("sql://sqlquery_") for ref in artifact.evidence_refs),
        "a2a_workflow_remains_complete": len(state.a2a_delegations) == 5
        and all(record.status == "completed" for record in state.a2a_delegations.values()),
        "task_waits_for_write_approval": state.status == "waiting_for_approval",
        "sql_tool_is_audited": any(
            record["tool_name"] == "query_market_database"
            and record["status"] == "completed"
            for record in state.tool_records
        ),
        "trace_links_react_policy_tool_and_a2a": all(
            marker in trace_text
            for marker in (
                '"event_type":"react_step"',
                '"event_type":"policy_decision"',
                '"component_name":"query_market_database"',
                '"event_type":"a2a_message"',
            )
        ),
    }
    report = {
        "version": "v21",
        "passed": all(checks.values()),
        "evidence_mode": "offline_model_fixture_with_real_sql_parser_and_sqlite",
        "live_model_called": False,
        "task_id": state.task_id,
        "run_id": state.run_id,
        "query_id": sql_research["query_id"],
        "checks": checks,
        "boundary": (
            "The model fixture makes a real tool-calling decision and the production SQLGlot/"
            "SQLite path executes it. This is not evidence of live DeepSeek SQL quality."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
