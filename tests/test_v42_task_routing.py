from pathlib import Path

from app.conversations.models import PendingRequestRecord
from app.conversations.repository import (
    CONVERSATION_SCHEMA_VERSION,
    ConversationRepository,
)
from app.copilot.task_router import TaskRelationRouter


def _session(repo: ConversationRepository, *, message: str):
    conversation = repo.create_conversation("tenant_demo", title=message)
    turn = repo.begin_turn(
        "tenant_demo",
        conversation.conversation_id,
        client_request_id=f"req_{conversation.conversation_id}",
        message=message,
    ).turn
    session = repo.create_task_session(
        "tenant_demo",
        conversation.conversation_id,
        turn.turn_id,
        intent="create_listing",
        title=message,
    )
    return conversation, turn, session


def test_new_business_request_does_not_answer_stale_clarification(tmp_path: Path) -> None:
    repo = ConversationRepository(tmp_path / "conversation.db")
    conversation, _, session = _session(repo, message="上架一款无线耳机")
    repo.set_active_task_session(
        "tenant_demo", conversation.conversation_id, session.task_session_id
    )
    repo.save_pending_request(
        "tenant_demo",
        conversation.conversation_id,
        task_session_id=session.task_session_id,
        checkpoint_thread_id=conversation.conversation_id,
        compiled_payload={"kind": "earphone"},
        clarification_round=1,
        last_question="请补充库存。",
    )
    pending = repo.get_pending_request("tenant_demo", conversation.conversation_id)

    decision = TaskRelationRouter().route(
        "请帮我查询机械键盘的市场价格。",
        sessions=[session],
        active_task_session_id=session.task_session_id,
        pending=pending,
    )

    assert decision.relation == "new_task"
    assert "new_business_action" in decision.evidence


def test_clarification_answer_continues_exact_pending_task(tmp_path: Path) -> None:
    repo = ConversationRepository(tmp_path / "conversation.db")
    conversation, _, session = _session(repo, message="上架一款无线耳机")
    pending = PendingRequestRecord(
        tenant_id="tenant_demo",
        conversation_id=conversation.conversation_id,
        task_session_id=session.task_session_id,
        checkpoint_thread_id=conversation.conversation_id,
        status="waiting_for_input",
        compiled_payload={},
        clarification_round=1,
        last_question="请补充库存。",
        created_at="2026-08-29T00:00:00Z",
        updated_at="2026-08-29T00:00:00Z",
    )

    decision = TaskRelationRouter().route(
        "库存800件，继续。",
        sessions=[session],
        active_task_session_id=session.task_session_id,
        pending=pending,
    )

    assert decision.relation == "continue_task"
    assert decision.target_task_session_id == session.task_session_id


def test_task_route_and_active_cursor_are_persisted(tmp_path: Path) -> None:
    repo = ConversationRepository(tmp_path / "conversation.db")
    conversation, turn, session = _session(repo, message="上架一款无线耳机")
    repo.set_active_task_session(
        "tenant_demo", conversation.conversation_id, session.task_session_id
    )
    decision = TaskRelationRouter().route(
        "继续补充库存",
        sessions=[session],
        active_task_session_id=session.task_session_id,
        pending=None,
    )
    repo.record_task_route(
        "tenant_demo", conversation.conversation_id, turn.turn_id, decision
    )

    refreshed = repo.get_conversation("tenant_demo", conversation.conversation_id)
    routes = repo.list_task_routes("tenant_demo", conversation.conversation_id)
    assert refreshed.active_task_session_id == session.task_session_id
    assert routes[0].relation == "continue_task"
    assert routes[0].protocol_version == "1.0"
    assert CONVERSATION_SCHEMA_VERSION == 13
