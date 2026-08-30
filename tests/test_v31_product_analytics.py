from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

import app.tools.analytics_tools as analytics_tools_module
from app.access.context import tenant_scope
from app.access.models import default_principal
from app.agents.analytics import AnalyticsAgent
from app.analytics.store import AnalyticsDataUnavailableError, AnalyticsStore
from app.analytics.time_range import parse_time_range
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.graph import V31ConversationGraph
from app.copilot.schemas import CopilotOutcome
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.policy import LlmPolicy
from app.model.tool_calling import ModelToolCall
from app.orchestration.planner import Planner
from app.orchestration.workflow import run_workflow
from app.orchestration.react_loop import BoundedReactLoop, ReactLoopConfig
from app.safety.policy_gateway import ToolPolicyGateway
from app.tools.governed_executor import GovernedToolExecutor
from app.tools.registry import ToolRegistry
from app.products.ledger import ProductLedger


def _seed_product(database_path: Path, tenant_id: str = "tenant_demo") -> AnalyticsStore:
    ConversationRepository(database_path)
    now = "2026-08-28T00:00:00+00:00"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO product_ledger(
                tenant_id, product_id, sku, title, category, status, source_task_id,
                seller_snapshot, created_at, updated_at
            ) VALUES(?, 'product_demo', 'SKU-DEMO', '游戏无线耳机', '无线耳机',
                'published', 'task_demo', '{}', ?, ?)""",
            (tenant_id, now, now),
        )
    store = AnalyticsStore(database_path)
    store.ensure_synthetic_history(
        tenant_id,
        "product_demo",
        price=300,
        initial_inventory=800,
        end_date=date(2026, 8, 28),
    )
    return store


def test_v31_schema_v4_and_synthetic_metrics_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "analytics.db"
    store = _seed_product(database_path)
    start, end = date(2026, 8, 1), date(2026, 8, 28)
    first = store.daily_metrics("tenant_demo", "product_demo", start, end)
    store.ensure_synthetic_history(
        "tenant_demo", "product_demo", price=999, initial_inventory=2, end_date=end
    )
    second = store.daily_metrics("tenant_demo", "product_demo", start, end)

    assert first == second
    assert first and {row.source_type for row in first} == {"synthetic_demo"}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 13
        assert connection.execute("SELECT COUNT(*) FROM daily_product_metrics").fetchone()[0] == 120


def test_v31_typed_sales_tool_matches_database_and_is_tenant_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "analytics.db"
    _seed_product(database_path)
    monkeypatch.setattr(
        analytics_tools_module, "AnalyticsStore", lambda: AnalyticsStore(database_path)
    )
    with tenant_scope("tenant_demo"):
        result = analytics_tools_module.get_sales_metrics(
            "product_demo", "2026-08-01", "2026-08-28"
        )
    with sqlite3.connect(database_path) as connection:
        units, revenue = connection.execute(
            """SELECT SUM(units_sold), SUM(revenue) FROM daily_product_metrics
            WHERE tenant_id='tenant_demo' AND product_id='product_demo'
            AND metric_date BETWEEN '2026-08-01' AND '2026-08-28'"""
        ).fetchone()

    assert result["metrics"]["units_sold"] == units
    assert result["metrics"]["revenue"] == revenue
    assert result["source_type"] == "synthetic_demo"
    assert result["source_updated_at"]
    with tenant_scope("tenant_beta"):
        with pytest.raises(AnalyticsDataUnavailableError):
            analytics_tools_module.get_sales_metrics(
                "product_demo", "2026-08-01", "2026-08-28"
            )


def test_v31_intent_and_time_range_are_policy_deterministic() -> None:
    compiler = RequestCompiler(ModelAdapter("deterministic", "test"))
    compiled = compiler.compile("上次那个游戏耳机最近30天销量和库存趋势怎么样")
    period = parse_time_range("上个月活动前后销量对比", today=date(2026, 8, 28))

    assert compiled.decision.intent.value == "product_performance"
    assert compiled.assessment.allowed_scopes == ["product.read", "analytics.read"]
    assert compiled.structured_request["period_label"] == "最近 30 天"
    assert compiled.structured_request["comparison_mode"] == "previous_period"
    assert period.label == "上个月"
    assert period.start_date == date(2026, 7, 1)
    assert period.end_date == date(2026, 7, 31)


class QueuedAdapter(ModelAdapter):
    def __init__(self, responses: list[ModelResponse]) -> None:
        super().__init__("deepseek", "deepseek-v4-pro", api_key="fixture")
        self.responses = responses

    def complete_with_tools(self, messages, tools, tool_choice="auto") -> ModelResponse:
        return self.responses.pop(0)


def _response(call_id: str, *, calls: list[ModelToolCall] | None = None) -> ModelResponse:
    calls = calls or []
    text = "" if calls else json.dumps(
        {
            "summary": "已按问题选择只读证据，业务数字以工具结果为准。",
            "selected_evidence_tools": ["get_sales_metrics", "get_inventory_history"],
            "caveats": ["当前为模拟演示数据"],
        },
        ensure_ascii=False,
    )
    message = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [call.to_api() for call in calls]
    return ModelResponse(
        call_id=call_id,
        provider="deepseek",
        model="deepseek-v4-pro",
        text=text,
        tool_calls=calls,
        assistant_message=message,
        finish_reason="tool_calls" if calls else "stop",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        usage_source="actual",
        prompt_tokens_estimate=10,
        completion_tokens_estimate=5,
        structured_output_mode="tool_calling",
    )


def test_v31_analytics_react_selects_only_read_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "analytics.db"
    _seed_product(database_path)
    monkeypatch.setattr(
        analytics_tools_module, "AnalyticsStore", lambda: AnalyticsStore(database_path)
    )
    arguments = {
        "product_id": "product_demo",
        "start_date": "2026-08-01",
        "end_date": "2026-08-28",
    }
    calls = [
        ModelToolCall(
            call_id="call_sales",
            name="get_sales_metrics",
            arguments=arguments,
            raw_arguments=json.dumps(arguments),
        ),
        ModelToolCall(
            call_id="call_inventory",
            name="get_inventory_history",
            arguments=arguments,
            raw_arguments=json.dumps(arguments),
        ),
    ]
    adapter = QueuedAdapter([_response("step_tools", calls=calls), _response("step_final")])
    registry = ToolRegistry()
    loop = BoundedReactLoop(
        adapter,
        GovernedToolExecutor(registry, ToolPolicyGateway()),
        ReactLoopConfig(max_steps=4, max_tool_calls=4),
    )
    policy = LlmPolicy(
        enabled_agents={"analytics_agent"},
        react_enabled_agents={"analytics_agent"},
        max_calls_per_agent=4,
        react_max_steps=4,
        react_max_tool_calls=4,
    )
    agent = AnalyticsAgent(
        registry,
        model_adapter=adapter,
        llm_policy=policy,
        react_loop=loop,
    )
    state = Planner().build_product_performance_state(
        "这个商品最近库存和销售情况",
        principal=default_principal(),
        constraints={
            **arguments,
            "period_label": "本月",
            "comparison_mode": "none",
        },
    )
    state.run_id = "run_analytics"
    with registry.agent_scope("analytics_agent", task_id=state.task_id, tenant_id="tenant_demo"):
        handoff = agent.run(state)

    assert handoff.status == "completed"
    assert handoff.result["selected_evidence_tools"] == [
        "get_inventory_history",
        "get_sales_metrics",
    ]
    assert handoff.result["source_type"] == "synthetic_demo"
    assert all(not record.side_effect for record in registry.records())
    assert {record.tool_name for record in registry.records()} == {
        "get_sales_metrics",
        "get_inventory_history",
    }


def test_v31_missing_metrics_fail_without_fabricated_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "analytics.db"
    _seed_product(database_path)
    monkeypatch.setattr(
        analytics_tools_module, "AnalyticsStore", lambda: AnalyticsStore(database_path)
    )
    registry = ToolRegistry()
    agent = AnalyticsAgent(
        registry,
        llm_policy=LlmPolicy(enabled_agents=set()),
    )
    state = Planner().build_product_performance_state(
        "查询未来销量",
        principal=default_principal(),
        constraints={
            "product_id": "product_demo",
            "start_date": (date(2030, 1, 1)).isoformat(),
            "end_date": (date(2030, 1, 7)).isoformat(),
            "period_label": "未来",
            "comparison_mode": "none",
        },
    )
    with registry.agent_scope("analytics_agent", task_id=state.task_id, tenant_id="tenant_demo"):
        with pytest.raises(AnalyticsDataUnavailableError):
            agent.run(state)
    assert "analytics_agent" not in state.agent_outputs


def test_v31_conversation_resolves_previous_product_and_runs_only_analytics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "conversation.db"
    repository = ConversationRepository(database_path)
    conversation = repository.create_conversation("tenant_demo", title="游戏耳机")
    listing_state = run_workflow(
        "我要上架一款成本95元的无线耳机，售价199元，库存800件，"
        "毛利率不低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟。",
        approved=True,
    )
    listing_state.conversation_id = conversation.conversation_id
    listing_state.turn_id = "turn_listing"
    product = ProductLedger(database_path).record_successful_execution(listing_state)
    monkeypatch.setattr(
        analytics_tools_module, "AnalyticsStore", lambda: AnalyticsStore(database_path)
    )
    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    checkpointer.setup()
    graph = V31ConversationGraph(repository=repository, checkpointer=checkpointer)

    response, steps, compiled = graph.invoke(
        "上次那个耳机最近30天销量、销售额和库存趋势怎么样？",
        principal=default_principal(),
        conversation_id=conversation.conversation_id,
        turn_id="turn_analytics",
    )

    analytics = response.panels[0].data
    assert compiled.decision.intent.value == "product_performance"
    assert response.outcome is CopilotOutcome.read_only_completed
    assert response.entity_refs == [product.product_id]
    assert steps == ["receive", "compile_request", "preflight_gate", "product_performance", "answer"]
    assert [panel.panel_id for panel in response.panels] == ["analytics"]
    assert response.store_modified is False
    assert response.approval_required is False
    assert response.action_summary.total_step_count == 1
    assert analytics["source_type"] == "synthetic_demo"
    assert "模拟演示数据" in response.assistant_message
    assert set(analytics["selected_evidence_tools"]) == {
        "get_sales_metrics",
        "compare_sales_periods",
        "get_inventory_history",
    }
