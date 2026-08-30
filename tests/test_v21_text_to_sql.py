from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.market import MarketAgent
from app.agents.supervisor import Supervisor
from app import linked_runtime
from app.main import sql_audits, sql_schema_catalog
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.policy import LlmPolicy
from app.model.runtime import SUPPORTED_LLM_AGENTS, SUPPORTED_REACT_AGENTS
from app.model.tool_calling import ModelToolCall
from app.orchestration.a2a import CapabilityDirectory
from app.orchestration.planner import Planner
from app.orchestration.react_loop import BoundedReactLoop, ReactLoopConfig
from app.safety.permissions import ToolPermissionError
from app.safety.policy_gateway import ToolPolicyGateway
from app.sql.database import MarketDatabase, SqlExecutionError
from app.sql.policy import SqlPolicyDecision, SqlPolicyDeniedError, SqlPolicyGateway
from app.sql.service import MarketSqlService
from app.tools.governed_executor import GovernedToolExecutor
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本95元、售价199元、库存800件的无线耳机，"
    "面向大学生，毛利率不低于25%。"
)
FINAL = json.dumps(
    {
        "insight_summary": "竞品均价189元，199元位于主流价格带内。",
        "query_rationale": "先用价格聚合验证目标售价的市场位置。",
        "recommended_product_ids": [],
    },
    ensure_ascii=False,
)


def tool_call(call_id: str, sql: str) -> ModelToolCall:
    arguments = {"sql": sql, "purpose": "student_price_analysis"}
    return ModelToolCall(
        call_id=call_id,
        name="query_market_database",
        arguments=arguments,
        raw_arguments=json.dumps(arguments),
    )


def model_response(
    call_id: str,
    *,
    text: str = "",
    calls: list[ModelToolCall] | None = None,
) -> ModelResponse:
    calls = calls or []
    assistant_message = {"role": "assistant", "content": text or None}
    if calls:
        assistant_message["tool_calls"] = [call.to_api() for call in calls]
    return ModelResponse(
        call_id=call_id,
        provider="deepseek",
        model="deepseek-v4-pro",
        text=text,
        tool_calls=calls,
        assistant_message=assistant_message,
        finish_reason="tool_calls" if calls else "stop",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        usage_source="actual",
        prompt_tokens_estimate=10,
        completion_tokens_estimate=5,
        structured_output_mode="tool_calling",
    )


class QueuedModelAdapter(ModelAdapter):
    def __init__(
        self,
        responses: list[ModelResponse],
        repair_response: ModelResponse | None = None,
    ) -> None:
        super().__init__(
            provider="deepseek", model="deepseek-v4-pro", api_key="fixture"
        )
        self.responses = list(responses)
        self.repair_response = repair_response
        self.requests: list[dict] = []
        self.repair_requests: list[dict] = []

    def complete_with_tools(self, messages, tools, tool_choice="auto") -> ModelResponse:
        self.requests.append(
            {
                "messages": messages,
                "tools": [tool.name for tool in tools],
                "tool_choice": tool_choice,
            }
        )
        return self.responses.pop(0)

    def repair_json(self, bad_text, error, json_schema) -> ModelResponse:
        self.repair_requests.append(
            {"bad_text": bad_text, "error": error, "json_schema": json_schema}
        )
        if self.repair_response is None:
            raise AssertionError("Unexpected JSON repair request")
        return self.repair_response


def make_market_agent(
    responses: list[ModelResponse],
    *,
    fallback_mode: str = "fail_closed",
    repair_response: ModelResponse | None = None,
    max_calls_per_agent: int = 2,
    react_steps: int = 2,
) -> tuple[MarketAgent, ToolRegistry, QueuedModelAdapter]:
    adapter = QueuedModelAdapter(responses, repair_response=repair_response)
    registry = ToolRegistry()
    loop = BoundedReactLoop(
        adapter,
        GovernedToolExecutor(registry, ToolPolicyGateway()),
        ReactLoopConfig(max_steps=react_steps, max_tool_calls=2),
    )
    policy = LlmPolicy(
        enabled_agents={"market_agent"},
        react_enabled_agents={"market_agent"},
        fallback_mode=fallback_mode,
        max_calls_per_agent=max_calls_per_agent,
        react_max_steps=react_steps,
        react_max_tool_calls=2,
    )
    return (
        MarketAgent(
            registry,
            model_adapter=adapter,
            llm_policy=policy,
            react_loop=loop,
        ),
        registry,
        adapter,
    )


def test_ast_policy_allows_aggregate_and_enforces_limit() -> None:
    decision = SqlPolicyGateway(max_rows=20).authorize(
        "SELECT AVG(price) AS avg_price, MIN(price) AS min_price "
        "FROM products"
    )

    assert decision.status == "allowed"
    assert decision.tables == ("products",)
    assert decision.columns == ("products.price",)
    assert decision.functions == ("AVG", "MIN")
    assert decision.normalized_sql.endswith("LIMIT 20")
    assert decision.limit_applied is True


def test_ast_policy_does_not_treat_boolean_connectors_as_functions() -> None:
    decision = SqlPolicyGateway(max_rows=20).authorize(
        "SELECT audience, COUNT(*) AS cnt, AVG(price) AS avg_price, "
        "MIN(price) AS min_price, MAX(price) AS max_price "
        "FROM product_audiences JOIN products "
        "ON product_audiences.product_id = products.id "
        "WHERE products.category = '无线耳机' AND audience = '大学生' "
        "GROUP BY audience"
    )

    assert decision.status == "allowed"
    assert decision.functions == ("AVG", "COUNT", "MAX", "MIN")
    assert decision.tables == ("product_audiences", "products")
    assert "product_audiences.tenant_id = 'tenant_demo'" in decision.normalized_sql
    assert "products.tenant_id = 'tenant_demo'" in decision.normalized_sql


def test_ast_policy_allows_safe_nullif_without_relaxing_unknown_functions() -> None:
    decision = SqlPolicyGateway().authorize(
        "SELECT ROUND(price / NULLIF(price, 0), 1) AS ratio "
        "FROM products WHERE category = '无线耳机'"
    )

    assert decision.functions == ("NULLIF", "ROUND")
    with pytest.raises(SqlPolicyDeniedError) as captured:
        SqlPolicyGateway().authorize("SELECT load_extension('/tmp/x') FROM products")
    assert captured.value.safe_to_retry is False


def test_ast_policy_allows_case_when_conditional_aggregation() -> None:
    decision = SqlPolicyGateway().authorize(
        "SELECT SUM(CASE WHEN price BETWEEN 150 AND 250 THEN 1 ELSE 0 END) "
        "AS in_target_band FROM products WHERE category = '无线耳机'"
    )

    assert decision.status == "allowed"
    assert decision.functions == ("SUM",)
    assert "CASE WHEN price BETWEEN 150 AND 250" in decision.normalized_sql


def test_sql_service_executes_join_with_sanitized_bounded_rows(tmp_path: Path) -> None:
    service = MarketSqlService(tmp_path / "market.db", max_rows=3)

    result = service.query(
        "SELECT p.name, AVG(r.rating) AS avg_rating "
        "FROM products p JOIN reviews r ON r.product_id = p.id "
        "GROUP BY p.id ORDER BY avg_rating DESC LIMIT 10",
        "rating_analysis",
    )

    assert result["row_count"] == 3
    assert result["policy"]["enforced_limit"] == 3
    assert result["policy"]["read_only_connection"] is True
    assert result["policy"]["tables"] == ["products", "reviews"]
    assert result["data_trust"] == "untrusted_market_data"


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM products", "select_only"),
        ("SELECT * FROM products", "wildcard_not_allowed"),
        ("SELECT name FROM sqlite_master", "table_not_allowed"),
        ("SELECT load_extension('/tmp/x') FROM products", "function_not_allowed"),
        ("SELECT name FROM products; SELECT text FROM reviews", "exactly_one_statement_required"),
        ("WITH x AS (SELECT name FROM products) SELECT name FROM x", "forbidden_ast_node"),
        ("SELECT name FROM products WHERE id IN (SELECT product_id FROM reviews)", "forbidden_ast_node"),
        ("SELECT secret FROM products", "column_not_allowed"),
    ],
)
def test_ast_policy_blocks_unsafe_or_out_of_scope_sql(sql: str, reason: str) -> None:
    gateway = SqlPolicyGateway()

    with pytest.raises(SqlPolicyDeniedError) as captured:
        gateway.authorize(sql)

    assert reason in captured.value.decision.reason_codes
    assert gateway.decisions[-1].status == "denied"


def test_sqlite_authorizer_is_second_defense_against_forged_update(
    tmp_path: Path,
) -> None:
    database = MarketDatabase(tmp_path / "market.db")
    forged = SqlPolicyDecision(
        status="allowed",
        query_hash="0" * 64,
        normalized_sql="UPDATE products SET price = 1",
        tables=("products",),
        columns=("products.price",),
        enforced_limit=10,
    )

    with pytest.raises(SqlExecutionError, match="not authorized"):
        database.execute(forged)


def test_sql_tool_is_available_only_to_market_agent() -> None:
    registry = ToolRegistry()
    with registry.agent_scope("market_agent", task_id="task_sql"):
        result = registry.call(
            "query_market_database",
            sql="SELECT COUNT(*) AS product_count FROM products",
            purpose="catalog_size",
        )
    assert result["rows"] == [{"product_count": 200}]

    with registry.agent_scope("strategy_agent", task_id="task_sql"):
        with pytest.raises(ToolPermissionError):
            registry.call(
                "query_market_database",
                sql="SELECT COUNT(*) AS product_count FROM products",
                purpose="unauthorized",
            )


def test_sql_service_audits_allowed_and_denied_queries(tmp_path: Path) -> None:
    service = MarketSqlService(tmp_path / "market.db")
    service.query("SELECT COUNT(*) AS count FROM products")
    with pytest.raises(SqlPolicyDeniedError):
        service.query("DROP TABLE products")

    audits = service.audits()
    assert [record["status"] for record in audits] == ["denied", "completed"]
    assert audits[0]["decision"]["reason_codes"] == ["select_only"]
    assert "rows" not in audits[1]


def test_market_agent_react_generates_sql_then_uses_result_in_artifact() -> None:
    query = (
        "SELECT AVG(price) AS avg_price, MIN(price) AS min_price, "
        "MAX(price) AS max_price FROM products"
    )
    agent, registry, adapter = make_market_agent(
        [
            model_response("model_sql", calls=[tool_call("call_sql", query)]),
            model_response("model_final", text=FINAL),
        ]
    )
    state = Planner().build_initial_state(GOAL)

    with registry.agent_scope("market_agent", task_id=state.task_id):
        handoff = agent.run(state)

    research = handoff.result["sql_research"]
    assert handoff.result["research_mode"] == "react_text_to_sql"
    assert research["rows"] == [
        {"avg_price": 379.0035, "min_price": 8.8, "max_price": 12999.0}
    ]
    assert research["policy"]["status"] == "allowed"
    assert handoff.result["evidence_refs"][-1].startswith("sql://sqlquery_")
    assert [record.tool_name for record in registry.records()] == [
        "query_market_database",
        "build_market_report",
    ]
    assert [message["role"] for message in adapter.requests[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert adapter.requests[1]["tool_choice"] == "none"


def test_market_agent_degrades_when_model_ignores_forced_finalization() -> None:
    first_query = "SELECT AVG(price) AS avg_price FROM products"
    second_query = (
        "SELECT id, price FROM products ORDER BY monthly_sales DESC LIMIT 3"
    )
    agent, registry, adapter = make_market_agent(
        [
            model_response(
                "market_query_one",
                calls=[tool_call("market_call_one", first_query)],
            ),
            model_response(
                "market_query_two",
                calls=[tool_call("market_call_two", second_query)],
            ),
            model_response("market_forced_final", text=FINAL),
        ],
        max_calls_per_agent=3,
        react_steps=3,
    )
    state = Planner().build_initial_state(GOAL)

    with registry.agent_scope("market_agent", task_id=state.task_id):
        handoff = agent.run(state)

    assert handoff.result["research_mode"] == "optional_evidence_degraded"
    assert handoff.result["evidence_status"] == "degraded"
    assert [request["tool_choice"] for request in adapter.requests] == ["auto", "none"]
    sql_records = [
        record
        for record in registry.records()
        if record.tool_name == "query_market_database"
    ]
    assert len(sql_records) == 1


def test_market_react_regenerates_sql_after_policy_rejection() -> None:
    corrected_query = "SELECT AVG(price) AS avg_price FROM products"
    agent, registry, adapter = make_market_agent(
        [
            model_response(
                "model_bad_sql",
                calls=[tool_call("call_bad", "SELECT secret FROM products")],
            ),
            model_response(
                "model_corrected_sql",
                calls=[tool_call("call_corrected", corrected_query)],
            ),
            model_response("model_final", text=FINAL),
        ]
    )
    state = Planner().build_initial_state(GOAL)

    with registry.agent_scope("market_agent", task_id=state.task_id):
        handoff = agent.run(state)

    assert handoff.result["research_mode"] == "react_text_to_sql"
    assert state.model_fallbacks == []
    assert [record.status for record in registry.records()[:2]] == [
        "failed",
        "completed",
    ]
    correction_messages = adapter.requests[1]["messages"]
    assert correction_messages[-1]["role"] == "tool"
    assert "sql_policy_denied" in correction_messages[-1]["content"]
    assert "allowed_tables_and_columns" in correction_messages[-1]["content"]


def test_market_react_repairs_explanatory_text_once_before_schema_validation() -> None:
    query = "SELECT AVG(price) AS avg_price FROM products"
    malformed = f"Market analysis follows.\n```json\n{FINAL}\n```"
    agent, registry, adapter = make_market_agent(
        [
            model_response("model_sql", calls=[tool_call("call_sql", query)]),
            model_response("model_malformed", text=malformed),
        ],
        repair_response=model_response("model_repair", text=FINAL),
        max_calls_per_agent=3,
    )
    state = Planner().build_initial_state(GOAL)

    with registry.agent_scope("market_agent", task_id=state.task_id):
        handoff = agent.run(state)

    assert handoff.result["research_mode"] == "react_text_to_sql"
    assert len(adapter.repair_requests) == 1
    assert [record["structured_validation"] for record in state.model_records[-2:]] == [
        "failed",
        "passed",
    ]
    assert state.model_records[-1]["purpose"] == "market_text_to_sql_react_repair"


def test_market_schema_failure_degrades_optional_evidence_after_single_repair() -> None:
    query = "SELECT AVG(price) AS avg_price FROM products"
    agent, registry, adapter = make_market_agent(
        [
            model_response("model_sql", calls=[tool_call("call_sql", query)]),
            model_response("model_malformed", text="analysis before json"),
        ],
        repair_response=model_response(
            "model_bad_repair", text='{"insight_summary": 123}'
        ),
        max_calls_per_agent=3,
    )
    state = Planner().build_initial_state(GOAL)

    with registry.agent_scope("market_agent", task_id=state.task_id):
        handoff = agent.run(state)

    assert len(adapter.repair_requests) == 1
    assert state.model_records[-1]["structured_validation"] == "failed"
    assert handoff.result["evidence_status"] == "degraded"
    assert state.degradations[-1].category == "model_protocol"


def test_unsafe_model_sql_falls_back_without_executing_query() -> None:
    agent, registry, _ = make_market_agent(
        [
            model_response(
                "model_bad_sql",
                calls=[tool_call("call_bad", "DELETE FROM products")],
            )
        ],
        fallback_mode="deterministic",
    )
    state = Planner().build_initial_state(GOAL)

    with registry.agent_scope("market_agent", task_id=state.task_id):
        handoff = agent.run(state)

    assert handoff.result["research_mode"] == "sql_policy_safe_fallback"
    assert handoff.result["sql_research"] is None
    assert state.model_fallbacks[0]["error_type"] == "SqlPolicyDeniedError"
    failed_sql = registry.records()[0]
    assert failed_sql.tool_name == "query_market_database"
    assert failed_sql.status == "failed"


def test_supervisor_full_workflow_persists_sql_research_artifact(
    monkeypatch,
) -> None:
    query = "SELECT AVG(price) AS avg_price FROM products"
    adapter = QueuedModelAdapter(
        [
            model_response("workflow_sql", calls=[tool_call("workflow_call", query)]),
            model_response("workflow_final", text=FINAL),
        ]
    )
    policy = LlmPolicy(
        enabled_agents={"market_agent"},
        react_enabled_agents={"market_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=2,
        react_max_steps=2,
    )
    monkeypatch.setattr("app.agents.supervisor.ModelAdapter", lambda **_kwargs: adapter)
    monkeypatch.setattr("app.agents.supervisor.load_llm_policy", lambda: policy)

    state = Supervisor().run(GOAL)

    assert state.agent_outputs["market_agent"]["research_mode"] == "react_text_to_sql"
    artifact = state.artifacts[state.latest_artifacts["market_agent"]]
    assert artifact.sql_research["policy"]["read_only_connection"] is True
    assert any(
        record["tool_name"] == "query_market_database"
        and record["status"] == "completed"
        for record in state.tool_records
    )


def test_supervisor_recovers_from_bad_sql_and_continues_workflow(monkeypatch) -> None:
    adapter = QueuedModelAdapter(
        [
            model_response(
                "workflow_bad_sql",
                calls=[tool_call("workflow_bad_call", "SELECT secret FROM products")],
            ),
            model_response(
                "workflow_corrected_sql",
                calls=[
                    tool_call(
                        "workflow_corrected_call",
                        "SELECT AVG(price) AS avg_price FROM products",
                    )
                ],
            ),
            model_response("workflow_corrected_final", text=FINAL),
        ]
    )
    policy = LlmPolicy(
        enabled_agents={"market_agent"},
        react_enabled_agents={"market_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=2,
        react_max_steps=2,
    )
    monkeypatch.setattr("app.agents.supervisor.ModelAdapter", lambda **_kwargs: adapter)
    monkeypatch.setattr("app.agents.supervisor.load_llm_policy", lambda: policy)

    state = Supervisor().run(GOAL)

    assert state.status == "waiting_for_approval"
    assert state.agent_outputs["market_agent"]["research_mode"] == "react_text_to_sql"
    sql_records = [
        record
        for record in state.tool_records
        if record["tool_name"] == "query_market_database"
    ]
    assert [record["status"] for record in sql_records] == ["failed", "completed"]
    assert state.model_fallbacks == []


def test_repeated_unsafe_sql_uses_safe_market_fallback_without_failing_workflow(
    monkeypatch,
) -> None:
    adapter = QueuedModelAdapter(
        [
            model_response(
                "failed_sql_1",
                calls=[tool_call("failed_call_1", "DELETE FROM products")],
            ),
            model_response(
                "failed_sql_2",
                calls=[tool_call("failed_call_2", "DELETE FROM products")],
            ),
        ]
    )
    policy = LlmPolicy(
        enabled_agents={"market_agent"},
        react_enabled_agents={"market_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=2,
        react_max_steps=2,
    )
    monkeypatch.setattr("app.agents.supervisor.ModelAdapter", lambda **_kwargs: adapter)
    monkeypatch.setattr("app.agents.supervisor.load_llm_policy", lambda: policy)

    state = Supervisor().run(GOAL)

    assert state.status == "waiting_for_approval"
    assert {record["call_id"] for record in state.model_records} == {
        "failed_sql_1",
        "failed_sql_2",
    }
    assert all(record["provider"] == "deepseek" for record in state.model_records)
    assert len(adapter.requests) == 2
    assert state.agent_outputs["market_agent"]["evidence_status"] == "degraded"
    assert state.model_fallbacks[-1]["error_type"] in {
        "SqlPolicyDeniedError",
        "ReactRepeatedActionError",
    }


def test_market_react_corrects_parallel_sql_batch_before_execution() -> None:
    first = "SELECT AVG(price) AS avg_price FROM products"
    second = "SELECT id, price FROM products ORDER BY monthly_sales DESC LIMIT 3"
    agent, registry, adapter = make_market_agent(
        [
            model_response(
                "parallel_sql",
                calls=[
                    tool_call("parallel_one", first),
                    tool_call("parallel_two", second),
                ],
            ),
            model_response(
                "selected_sql", calls=[tool_call("selected_one", first)]
            ),
            model_response("selected_final", text=FINAL),
        ],
        max_calls_per_agent=3,
        react_steps=2,
    )
    state = Planner().build_initial_state(GOAL)

    with registry.agent_scope("market_agent", task_id=state.task_id):
        handoff = agent.run(state)

    assert handoff.result["research_mode"] == "react_text_to_sql"
    assert handoff.result["evidence_status"] == "enhanced"
    sql_records = [
        record for record in registry.records()
        if record.tool_name == "query_market_database"
    ]
    assert len(sql_records) == 1
    correction = adapter.requests[1]["messages"][-1]
    assert correction["role"] == "tool"
    assert "exactly_one_market_query_required" in correction["content"]


def test_full_workflow_replays_real_parallel_sql_response_without_failing(
    monkeypatch,
) -> None:
    query = "SELECT AVG(price) AS avg_price FROM products"
    adapter = QueuedModelAdapter(
        [
            model_response(
                "workflow_parallel_sql",
                calls=[
                    tool_call("workflow_parallel_one", query),
                    tool_call(
                        "workflow_parallel_two",
                        "SELECT id, price FROM products ORDER BY monthly_sales DESC LIMIT 3",
                    ),
                ],
            ),
            model_response(
                "workflow_selected_sql",
                calls=[tool_call("workflow_selected_one", query)],
            ),
            model_response("workflow_selected_final", text=FINAL),
        ]
    )
    policy = LlmPolicy(
        enabled_agents={"market_agent"},
        react_enabled_agents={"market_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=3,
        react_max_steps=2,
        react_max_tool_calls=2,
    )
    monkeypatch.setattr("app.agents.supervisor.ModelAdapter", lambda **_kwargs: adapter)
    monkeypatch.setattr("app.agents.supervisor.load_llm_policy", lambda: policy)

    state = Supervisor().run(GOAL)

    assert state.status == "waiting_for_approval"
    assert state.outcome.value == "awaiting_approval"
    assert state.failure is None
    assert state.agent_outputs["market_agent"]["evidence_status"] == "enhanced"
    sql_records = [
        record
        for record in state.tool_records
        if record["tool_name"] == "query_market_database"
    ]
    assert len(sql_records) == 1
    assert {record["call_id"] for record in state.model_records} == {
        "workflow_parallel_sql",
        "workflow_selected_sql",
        "workflow_selected_final",
    }


def test_capability_and_api_catalog_expose_sql_as_read_only() -> None:
    _, capability = CapabilityDirectory().discover(
        "market.research", expected_agent="market_agent"
    )
    schema = sql_schema_catalog()

    assert "query_market_database" in capability.allowed_tools
    assert schema["access"] == "select_only"
    assert "NULLIF" in schema["allowed_functions"]
    assert schema["tables"]["products"] == [
        "category",
        "id",
        "monthly_sales",
        "name",
        "price",
    ]
    assert isinstance(sql_audits(limit=5), list)


def test_market_agent_is_a_supported_llm_and_react_runtime() -> None:
    assert "market_agent" in SUPPORTED_LLM_AGENTS
    assert "market_agent" in SUPPORTED_REACT_AGENTS
    assert "analytics_agent" in SUPPORTED_LLM_AGENTS
    assert "analytics_agent" in SUPPORTED_REACT_AGENTS


def test_linked_runtime_requires_all_v31_react_agents(monkeypatch) -> None:
    llm_status = {
        "provider": "deepseek",
        "real_llm_enabled": True,
        "ready": True,
        "issues": [],
        "enabled_agents": [
            "market_agent",
            "listing_agent",
            "strategy_agent",
            "review_agent",
            "analytics_agent",
        ],
        "react_enabled_agents": [],
        "fallback_mode": "fail_closed",
    }
    monkeypatch.setattr(
        linked_runtime, "get_llm_runtime_status", lambda: dict(llm_status)
    )
    monkeypatch.setattr(
        linked_runtime,
        "get_browser_runtime_status",
        lambda: {
            "backend": "playwright",
            "real_browser_enabled": True,
            "ready": True,
            "issues": [],
        },
    )

    blocked = linked_runtime.get_linked_runtime_status()
    assert blocked["ready"] is False
    assert "missing_react_agents:strategy_agent" in blocked["issues"]

    llm_status["react_enabled_agents"] = ["market_agent"]
    assert "missing_react_agents:strategy_agent" in linked_runtime.get_linked_runtime_status()["issues"]

    llm_status["react_enabled_agents"] = ["strategy_agent"]
    assert linked_runtime.get_linked_runtime_status()["ready"] is True
