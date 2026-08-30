from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.access.models import default_principal
from app.conversations.repository import (
    CONVERSATION_SCHEMA_VERSION,
    ConversationRepository,
)
from app.copilot.compiler import RequestCompiler
from app.copilot.graph import V33ConversationGraph
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.model.adapter import ModelAdapter


def _compiler() -> RequestCompiler:
    return RequestCompiler(ModelAdapter(provider="deterministic", model="local-rule-v6"))


def test_two_tasks_in_one_conversation_have_independent_langgraph_threads(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo", title="multi task")
    connection = sqlite3.connect(tmp_path / "threads.db", check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    graph = V33ConversationGraph(
        compiler=_compiler(), repository=repository, checkpointer=saver
    )

    first, _, _ = graph.invoke(
        "帮我上架无线耳机。",
        principal=default_principal(),
        conversation_id=conversation.conversation_id,
        turn_id="turn_task_a",
        thread_id="session_task_a",
    )
    second, _, _ = graph.invoke(
        "帮我上架机械键盘。",
        principal=default_principal(),
        conversation_id=conversation.conversation_id,
        turn_id="turn_task_b",
        thread_id="session_task_b",
    )

    assert first.outcome is CopilotOutcome.waiting_for_input
    assert second.outcome is CopilotOutcome.waiting_for_input
    assert first.thread_id == "session_task_a"
    assert second.thread_id == "session_task_b"
    before_b = connection.execute(
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id='session_task_b'"
    ).fetchone()[0]

    resumed, _, compiled = graph.resume(
        "成本95元，库存800件。",
        conversation_id=conversation.conversation_id,
        turn_id="turn_task_a_followup",
        thread_id="session_task_a",
    )

    after_b = connection.execute(
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id='session_task_b'"
    ).fetchone()[0]
    assert resumed.thread_id == "session_task_a"
    assert compiled.structured_request["category"] == "无线耳机"
    assert after_b == before_b
    connection.close()


def test_suspended_pending_tasks_keep_distinct_recovery_keys(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo", title="multi task")
    sessions = []
    for index, title in enumerate(("上架无线耳机", "上架机械键盘"), 1):
        turn = repository.begin_turn(
            "tenant_demo",
            conversation.conversation_id,
            client_request_id=f"req_task_{index}",
            message=title,
        ).turn
        session = repository.create_task_session(
            "tenant_demo",
            conversation.conversation_id,
            turn.turn_id,
            intent="create_listing",
            title=title,
        )
        sessions.append(session)
        repository.save_pending_request(
            "tenant_demo",
            conversation.conversation_id,
            task_session_id=session.task_session_id,
            checkpoint_thread_id=session.checkpoint_thread_id,
            compiled_payload={"category": title},
            clarification_round=1,
            last_question="请补充成本、售价和库存。",
        )

    repository.set_task_pending_status(
        "tenant_demo", sessions[0].task_session_id, "suspended"
    )
    first = repository.get_task_pending_request(
        "tenant_demo", sessions[0].task_session_id
    )
    second = repository.get_task_pending_request(
        "tenant_demo", sessions[1].task_session_id
    )

    assert first is not None and second is not None
    assert first.status == "suspended"
    assert first.checkpoint_thread_id == sessions[0].task_session_id
    assert second.checkpoint_thread_id == sessions[1].task_session_id
    assert first.checkpoint_thread_id != second.checkpoint_thread_id
    assert CONVERSATION_SCHEMA_VERSION == 13


def test_facade_switches_between_two_interrupted_tasks_without_state_leak(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    facade = ConversationFacade(repository=repository)
    principal = default_principal()

    first = facade.handle_message(
        "帮我上架无线耳机。",
        principal=principal,
        client_request_id="req_facade_task_a",
    )
    second = facade.handle_message(
        "请帮我上架机械键盘。",
        principal=principal,
        conversation_id=first.conversation_id,
        client_request_id="req_facade_task_b",
    )
    detail = repository.get_detail("tenant_demo", first.conversation_id)
    earphone = next(item for item in detail.task_sessions if "无线耳机" in item.title)
    keyboard = next(item for item in detail.task_sessions if "机械键盘" in item.title)

    resumed = facade.handle_message(
        "回到之前的无线耳机任务，成本95元，库存800件。",
        principal=principal,
        conversation_id=first.conversation_id,
        client_request_id="req_facade_task_a_resume",
    )

    active = repository.get_conversation("tenant_demo", first.conversation_id)
    earphone_pending = repository.get_task_pending_request(
        "tenant_demo", earphone.task_session_id
    )
    keyboard_pending = repository.get_task_pending_request(
        "tenant_demo", keyboard.task_session_id
    )
    assert first.outcome is CopilotOutcome.waiting_for_input
    assert second.outcome is CopilotOutcome.waiting_for_input
    assert resumed.outcome is CopilotOutcome.waiting_for_input
    assert active.active_task_session_id == earphone.task_session_id
    assert earphone_pending is not None
    assert earphone_pending.compiled_payload["structured_request"]["category"] == "无线耳机"
    assert keyboard_pending is not None and keyboard_pending.status == "suspended"
    assert earphone.checkpoint_thread_id != keyboard.checkpoint_thread_id
