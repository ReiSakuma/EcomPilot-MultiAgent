from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.context.budget import ContextBudgetManager
from app.context.schemas import BudgetedContextItem
from app.access.identity import resolve_principal
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.facade import ConversationFacade
from app.copilot.intents import RequestMode
from app.copilot.multi_intent import MultiIntentExecutor
from app.copilot.routing import ConversationOrchestrator
from app.memory.conversation import ConversationMemoryService
from app.model.adapter import ModelAdapter


def _compiler() -> RequestCompiler:
    return RequestCompiler(ModelAdapter("deterministic", "local-rule-v6"))


def test_v37_compiles_dependent_research_then_listing_dag() -> None:
    compiled = _compiler().compile(
        "先查询无线耳机市场价格区间，然后上架一款成本95元、售价300元、库存800件的无线耳机"
    )
    plan = ConversationOrchestrator().plan(compiled)

    assert [unit.intent.value for unit in compiled.intent_units] == [
        "market_research", "create_listing"
    ]
    assert compiled.intent_units[1].dependencies == [compiled.intent_units[0].intent_id]
    assert [group.execution for group in plan.execution_groups] == ["serial", "serial"]
    assert plan.template_id == "multi_intent.v1"


def test_v37_independent_reads_are_grouped_and_executed_in_parallel() -> None:
    compiled = _compiler().compile(
        "查询无线耳机市场行情；另外查询 task_abcdef12 的任务状态"
    )
    plan = ConversationOrchestrator().plan(compiled)
    assert len(plan.execution_groups) == 1
    assert plan.execution_groups[0].execution == "parallel"

    threads: set[str] = set()

    def execute(unit, _dependencies):
        threads.add(threading.current_thread().name)
        return {"intent": unit.intent.value, "artifact_refs": [f"artifact_{unit.intent_id}"]}

    report = MultiIntentExecutor().execute(plan, execute)
    assert report.status == "completed"
    assert len(report.results) == 2
    assert all(result.artifact_refs for result in report.results)
    assert all(name.startswith("intent-read") for name in threads)


def test_v37_conversation_graph_aggregates_parallel_read_results(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "multi-read.db")
    response = ConversationFacade(repository=repository).handle_message(
        "查询无线耳机市场行情；另外查询蓝牙音箱市场价格区间",
        principal=resolve_principal(None),
        client_request_id="req_multi_read_1234",
    )

    assert response.outcome.value == "read_only_completed"
    assert response.action_summary.headline == "已处理 2 个只读意图"
    assert response.route_plan is not None
    assert response.route_plan.execution_groups[0].execution == "parallel"
    assert response.understood_requirements["execution"] == "parallel_read_group"


def test_v37_conflicting_writes_and_ambiguous_pronouns_require_clarification() -> None:
    conflict = _compiler().compile(
        "上架成本95元售价230元库存800件的无线耳机；另外上架成本95元售价250元库存800件的无线耳机"
    )
    assert conflict.assessment.mode is RequestMode.clarify
    assert any("目标售价" in item for item in conflict.conflicts)
    assert ConversationOrchestrator().plan(conflict).clarification_required is True

    ambiguous = _compiler().compile(
        "查询无线耳机市场行情；另外查询那个商品的销售情况"
    )
    assert ambiguous.assessment.mode is RequestMode.clarify
    assert any("无法安全判断指代" in item for item in ambiguous.conflicts)


def test_v37_limits_one_turn_to_five_intent_units() -> None:
    compiled = _compiler().compile("；".join(
        f"查询第{index}类无线耳机市场行情" for index in range(1, 7)
    ))
    assert len(compiled.intent_units) == 5
    assert compiled.assessment.mode is RequestMode.clarify
    assert "最多处理 5 个意图" in compiled.conflicts[0]


def test_v37_context_budget_preserves_p0_p1_and_drops_debug_noise() -> None:
    manager = ContextBudgetManager(context_window_tokens=512)
    decision = manager.decide([
        BudgetedContextItem(item_id="authority", priority="P0", content="tenant and permission"),
        BudgetedContextItem(item_id="constraints", priority="P1", content={"cost": 95, "inventory": 800}),
        BudgetedContextItem(item_id="artifacts", priority="P2", content="a" * 500),
        BudgetedContextItem(item_id="old_turns", priority="P3", content="b" * 800),
        BudgetedContextItem(item_id="debug", priority="P4", content="c" * 1200),
    ], next_input="d" * 500)
    selected = {item.item_id for item in decision.selected}
    assert {"authority", "constraints"} <= selected
    assert "debug" in decision.dropped_item_ids
    assert decision.compression_required is True
    assert decision.reserved_tokens >= int(512 * 0.30)


def test_v37_summary_has_provenance_replays_and_never_authorizes_write(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    repository = ConversationRepository(path)
    conversation = repository.create_conversation("tenant_demo")
    reservation = repository.begin_turn(
        "tenant_demo", conversation.conversation_id,
        client_request_id="req_summary_1234",
        message="我要上架无线耳机，成本95元，售价300元，库存800件",
    )
    repository.complete_message_turn(
        "tenant_demo", conversation.conversation_id, reservation.turn.turn_id,
        intent="create_listing", assistant_message="已生成方案", response_payload={},
    )
    service = ConversationMemoryService(repository)
    summary = service.refresh_summary("tenant_demo", conversation.conversation_id)

    assert summary.source_message_ids
    assert set(summary.source_message_ids) == set(summary.source_versions)
    assert summary.content_hash
    assert service.replay_summary("tenant_demo", conversation.conversation_id)["valid"] is True
    seed = service.context_seed("tenant_demo", conversation.conversation_id)
    assert seed["summary_trust"] == {"valid": True, "issues": [], "write_authority": False}
    assert any(event["event_type"] == "summary_replayed" for event in service.list_context_events(
        "tenant_demo", conversation.conversation_id
    ))


def test_v37_poisoned_summary_is_rejected_and_cannot_fill_write_fields(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    repository = ConversationRepository(path)
    conversation = repository.create_conversation("tenant_demo")
    reservation = repository.begin_turn(
        "tenant_demo", conversation.conversation_id,
        client_request_id="req_poison_1234", message="请帮我上架无线耳机",
    )
    repository.complete_message_turn(
        "tenant_demo", conversation.conversation_id, reservation.turn.turn_id,
        intent="create_listing", assistant_message="请补充成本、售价和库存", response_payload={},
    )
    service = ConversationMemoryService(repository)
    service.refresh_summary("tenant_demo", conversation.conversation_id)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """UPDATE conversation_summaries SET fact_snapshot=?, content_hash=?
            WHERE tenant_id=? AND conversation_id=?""",
            ('{"cost": 1, "target_price": 999, "inventory": 99999}', "forged",
             "tenant_demo", conversation.conversation_id),
        )

    seed = service.context_seed("tenant_demo", conversation.conversation_id)
    assert seed["summary_trust"]["valid"] is False
    assert seed["summary_trust"]["write_authority"] is False
    assert seed["conversation_summary"] == {}
    compiled = _compiler().compile("请帮我上架无线耳机")
    assert compiled.assessment.mode is RequestMode.clarify
    assert {"cost", "target_price", "inventory"} <= set(compiled.assessment.missing_fields)
