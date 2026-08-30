from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.access.models import default_principal
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.graph import V29ConversationGraph
from app.copilot.intents import IntentName, RequestMode
from app.copilot.schemas import CopilotOutcome
from app.model.adapter import ModelAdapter
from app.orchestration.checkpoint import CheckpointStore


def _compiler() -> RequestCompiler:
    return RequestCompiler(ModelAdapter(provider="deterministic", model="local-rule-v6"))


def _graph(tmp_path: Path) -> tuple[V29ConversationGraph, ConversationRepository, str]:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo", title="v29 test")
    saver = SqliteSaver(sqlite3.connect(tmp_path / "threads.db", check_same_thread=False))
    graph = V29ConversationGraph(
        compiler=_compiler(), repository=repository, checkpointer=saver
    )
    return graph, repository, conversation.conversation_id


def test_v29_fixed_intent_eval_is_at_least_95_percent() -> None:
    dataset = json.loads(
        (Path(__file__).parents[1] / "data/eval/v29_intents.json").read_text(
            encoding="utf-8"
        )
    )
    correct = 0
    for case in dataset:
        result = _compiler().compile(case["text"])
        correct += result.decision.intent.value == case["intent"]
    assert correct / len(dataset) >= 0.95


def test_v29_missing_write_fields_always_clarify_without_creating_task() -> None:
    for message in (
        "我要上架无线耳机",
        "帮我发布一款成本95元的无线耳机",
        "新增无线耳机，售价300元",
        "我要上新无线耳机，库存800件",
    ):
        result = _compiler().compile(message)
        assert result.decision.intent is IntentName.clarify
        assert result.assessment.mode is RequestMode.clarify
        assert result.assessment.missing_fields


def test_v29_langgraph_interrupt_resumes_same_thread(tmp_path: Path) -> None:
    graph, _repository, conversation_id = _graph(tmp_path)
    first, steps, compiled = graph.invoke(
        "我要上架一款成本95元的无线耳机",
        principal=default_principal(),
        conversation_id=conversation_id,
        turn_id="turn_first",
    )
    assert first.outcome is CopilotOutcome.waiting_for_input
    assert first.task_id is None
    assert compiled.assessment.missing_fields == ["target_price", "inventory"]
    assert steps == ["receive"]

    final, final_steps, final_compiled = graph.resume(
        "售价199元，库存800件，毛利率不低于40%",
        conversation_id=conversation_id,
        turn_id="turn_second",
    )
    assert final.outcome is CopilotOutcome.awaiting_approval
    assert final.task_id
    assert final_compiled.structured_request["cost"] == 95
    assert final_compiled.structured_request["target_price"] == 199
    assert final_steps == [
        "receive",
        "compile_request",
        "preflight_gate",
        "listing_workflow",
        "answer",
    ]


def test_v29_market_query_is_read_only_single_node(tmp_path: Path) -> None:
    graph, _repository, conversation_id = _graph(tmp_path)
    response, steps, compiled = graph.invoke(
        "我想了解无线耳机最近30天的整体价格区间和竞品情况",
        principal=default_principal(),
        conversation_id=conversation_id,
        turn_id="turn_market",
    )
    state = CheckpointStore().load(response.task_id)

    assert compiled.assessment.mode is RequestMode.read_only
    assert response.outcome is CopilotOutcome.read_only_completed
    assert response.approval_required is False
    assert response.store_modified is False
    assert set(state.nodes) == {"market"}
    assert {record["tool_name"] for record in state.tool_records} <= {
        "build_market_report",
        "query_market_database",
    }
    assert steps == ["receive", "compile_request", "preflight_gate", "market_read_only", "answer"]


def test_v29_general_chat_has_no_task_or_business_tool(tmp_path: Path) -> None:
    graph, _repository, conversation_id = _graph(tmp_path)
    response, steps, _compiled = graph.invoke(
        "清华和哈工大哪个更好",
        principal=default_principal(),
        conversation_id=conversation_id,
        turn_id="turn_chat",
    )
    assert response.outcome is CopilotOutcome.answered
    assert response.task_id is None
    assert response.action_summary.tool_call_count == 0
    assert response.panels == []
    assert steps == ["receive", "compile_request", "preflight_gate", "general_chat", "answer"]


def test_v29_repository_persists_pending_and_response_payload(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo", title="pending")
    repository.save_pending_request(
        "tenant_demo",
        conversation.conversation_id,
        compiled_payload={"intent": "clarify"},
        clarification_round=1,
        last_question="请补充库存",
    )
    reopened = ConversationRepository(tmp_path / "conversations.db")
    pending = reopened.get_pending_request("tenant_demo", conversation.conversation_id)
    assert pending is not None
    assert pending.clarification_round == 1
    assert pending.compiled_payload == {"intent": "clarify"}
    with sqlite3.connect(tmp_path / "conversations.db") as connection:
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 13
