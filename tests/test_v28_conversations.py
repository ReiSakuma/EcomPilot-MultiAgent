from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from app.access.models import AccessPrincipal, default_principal
from app.conversations.repository import (
    ConversationConflictError,
    ConversationNotFoundError,
    ConversationRepository,
)
from app.copilot.facade import ConversationFacade
from app.copilot.graph import V28ConversationGraph
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.workflow import run_workflow
from scripts.backfill_v28_conversations import backfill


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，主要面向游戏爱好者，"
    "库存800件，毛利率不能低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认的产品形态：未确认。运营目标：主打性价比。"
)


def _completed_state(conversation_id: str, turn_id: str):
    state = run_workflow(GOAL, approved=False)
    state.conversation_id = conversation_id
    state.turn_id = turn_id
    state.intent = "create_listing"
    return state


def test_v28_repository_migrates_and_restores_after_reopen(tmp_path: Path) -> None:
    database = tmp_path / "conversations.db"
    repository = ConversationRepository(database)
    conversation = repository.create_conversation("tenant_demo", title="无线耳机上新")
    reservation = repository.begin_turn(
        "tenant_demo",
        conversation.conversation_id,
        client_request_id="req_restore_001",
        message=GOAL,
    )
    state = _completed_state(conversation.conversation_id, reservation.turn.turn_id)
    repository.complete_turn(state, "方案已经生成，等待确认。")

    reopened = ConversationRepository(database)
    summaries = reopened.list_conversations("tenant_demo")
    detail = reopened.get_detail("tenant_demo", conversation.conversation_id)

    assert summaries[0].conversation_id == conversation.conversation_id
    assert summaries[0].last_task_status == "awaiting_approval"
    assert summaries[0].message_count == 2
    assert [message.role for message in detail.messages] == ["user", "assistant"]
    assert detail.tasks[0].task_id == state.task_id
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 13


def test_v28_client_request_id_is_idempotent_and_content_bound(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo")
    first = repository.begin_turn(
        "tenant_demo",
        conversation.conversation_id,
        client_request_id="req_idempotent_001",
        message=GOAL,
    )
    state = _completed_state(conversation.conversation_id, first.turn.turn_id)
    repository.complete_turn(state, "方案已经生成。")

    duplicate = repository.begin_turn(
        "tenant_demo",
        conversation.conversation_id,
        client_request_id="req_idempotent_001",
        message=GOAL,
    )

    assert duplicate.created is False
    assert duplicate.turn.task_id == state.task_id
    assert len(repository.get_detail("tenant_demo", conversation.conversation_id).turns) == 1
    with pytest.raises(ConversationConflictError):
        repository.begin_turn(
            "tenant_demo",
            conversation.conversation_id,
            client_request_id="req_idempotent_001",
            message="不同内容",
        )


def test_v28_all_conversation_queries_are_tenant_scoped(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo", title="A 的会话")

    assert repository.list_conversations("tenant_beta") == []
    with pytest.raises(ConversationNotFoundError):
        repository.get_detail("tenant_beta", conversation.conversation_id)
    with pytest.raises(ConversationNotFoundError):
        repository.begin_turn(
            "tenant_beta",
            conversation.conversation_id,
            client_request_id="req_cross_tenant",
            message=GOAL,
        )


def test_v28_facade_duplicate_submission_returns_same_task(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    facade = ConversationFacade(repository=repository)
    first = facade.handle_message(
        GOAL,
        principal=default_principal(),
        client_request_id="req_facade_001",
    )
    duplicate = facade.handle_message(
        GOAL,
        principal=default_principal(),
        conversation_id=first.conversation_id,
        client_request_id="req_facade_001",
    )

    assert first.task_id == duplicate.task_id
    assert first.turn_id == duplicate.turn_id
    assert first.conversation_id == duplicate.conversation_id
    detail = repository.get_detail("tenant_demo", first.conversation_id)
    assert len(detail.turns) == 1
    assert len(detail.tasks) == 1


def test_v28_langgraph_checkpointer_persists_by_conversation_thread(tmp_path: Path) -> None:
    database = tmp_path / "threads.db"
    connection = sqlite3.connect(database, check_same_thread=False)
    saver = SqliteSaver(connection)
    graph = V28ConversationGraph(checkpointer=saver)

    response, steps = graph.invoke(
        GOAL,
        principal=default_principal(),
        conversation_id="conv_thread_test",
        turn_id="turn_thread_test",
    )

    assert response.conversation_id == "conv_thread_test"
    assert response.thread_id == "conv_thread_test"
    assert steps == ["receive", "legacy_listing_workflow", "answer"]
    with sqlite3.connect(database) as verification:
        count = verification.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
            ("conv_thread_test",),
        ).fetchone()[0]
    assert count >= 3
    connection.close()


def test_v28_old_checkpoint_defaults_and_backfill(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "checkpoints"
    store = CheckpointStore(checkpoint_dir)
    state = run_workflow(GOAL, approved=False)
    payload = state.model_dump(mode="json")
    for field in ("conversation_id", "turn_id", "intent", "entity_refs"):
        payload.pop(field, None)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (checkpoint_dir / f"{state.task_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = store.load(state.task_id)
    assert loaded.conversation_id is None
    assert loaded.intent == "create_listing"
    report = backfill(checkpoint_dir, tmp_path / "conversations.db")
    migrated = store.load(state.task_id)

    assert report["indexed"] == 1
    assert report["errors"] == []
    assert migrated.conversation_id is not None
    assert migrated.turn_id is not None
    detail = ConversationRepository(tmp_path / "conversations.db").get_detail(
        "tenant_demo",
        migrated.conversation_id,
    )
    assert detail.tasks[0].task_id == state.task_id


def test_v28_approval_is_preserved_as_a_followup_assistant_message(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    facade = ConversationFacade(repository=repository)
    initial = facade.handle_message(
        GOAL,
        principal=default_principal(),
        client_request_id="req_approval_history",
    )
    completed = facade.approve(initial.task_id, principal=default_principal())
    detail = ConversationRepository(tmp_path / "conversations.db").get_detail(
        "tenant_demo",
        initial.conversation_id,
    )

    assert completed.store_modified is True
    assert detail.tasks[0].outcome == "completed"
    assert [message.role for message in detail.messages] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert "同步到模拟店铺" in detail.messages[-1].content


def test_v28_tenant_identity_model_can_represent_second_merchant() -> None:
    beta = AccessPrincipal(
        subject_id="merchant-beta",
        tenant_id="tenant_beta",
        roles=("operator",),
    )
    assert beta.tenant_id != default_principal().tenant_id


def test_v28_api_conversation_listing_enforces_tenant_scope(
    tmp_path: Path, monkeypatch
) -> None:
    import app.main as main_module

    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo", title="租户 A 历史")
    monkeypatch.setattr(main_module, "ConversationRepository", lambda: repository)

    visible = main_module.list_copilot_conversations(
        limit=50,
        authorization="Bearer demo-merchant-a"
    )
    hidden = main_module.list_copilot_conversations(
        limit=50,
        authorization="Bearer demo-merchant-b"
    )

    assert [item.conversation_id for item in visible.conversations] == [
        conversation.conversation_id
    ]
    assert hidden.conversations == []
    with pytest.raises(main_module.HTTPException) as error:
        main_module.get_copilot_conversation(
            conversation.conversation_id,
            authorization="Bearer demo-merchant-b",
        )
    assert error.value.status_code == 404
