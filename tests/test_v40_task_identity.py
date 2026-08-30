from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.access.models import AccessPrincipal
from app.conversations.repository import (
    CONVERSATION_SCHEMA_VERSION,
    ConversationNotFoundError,
    ConversationRepository,
)
from app.orchestration.failures import TaskOutcome
from app.orchestration.state import TaskState


def _principal(tenant_id: str = "tenant_demo") -> AccessPrincipal:
    return AccessPrincipal(
        subject_id="operator-demo",
        tenant_id=tenant_id,
        roles=("operator",),
    )


def _turn(repo: ConversationRepository, tenant_id: str, message: str = "上架耳机"):
    conversation = repo.create_conversation(tenant_id, title=message)
    reservation = repo.begin_turn(
        tenant_id,
        conversation.conversation_id,
        client_request_id=f"req_{conversation.conversation_id}",
        message=message,
    )
    return conversation, reservation.turn


def test_v40_schema_is_migrated_on_an_existing_database(tmp_path: Path) -> None:
    database_path = tmp_path / "conversation.db"
    sqlite3.connect(database_path).execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    ).close()

    ConversationRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        versions = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
    assert {"task_sessions", "workflow_runs", "batch_jobs", "turn_task_links"} <= tables
    assert CONVERSATION_SCHEMA_VERSION == 13
    assert CONVERSATION_SCHEMA_VERSION in versions


def test_legacy_task_state_is_atomically_projected_and_updated(tmp_path: Path) -> None:
    repo = ConversationRepository(tmp_path / "conversation.db")
    principal = _principal()
    conversation, turn = _turn(repo, principal.tenant_id)
    state = TaskState(
        task_id="task_v40demo",
        run_id="run_v40demo",
        goal="上架一款无线耳机",
        conversation_id=conversation.conversation_id,
        turn_id=turn.turn_id,
        principal=principal,
        intent="create_listing",
        outcome=TaskOutcome.awaiting_approval,
        state_version=4,
        checkpoint_version=3,
    )

    repo.complete_turn(state, "方案等待确认", response_payload={"status": "waiting"})
    first = repo.get_detail(principal.tenant_id, conversation.conversation_id)

    assert len(first.task_sessions) == 1
    assert len(first.workflow_runs) == 1
    session = first.task_sessions[0]
    workflow = first.workflow_runs[0]
    assert session.status == "awaiting_approval"
    assert session.current_task_id == state.task_id
    assert session.active_workflow_run_id == workflow.workflow_run_id
    assert workflow.checkpoint_thread_id == session.task_session_id
    assert workflow.checkpoint_namespace == state.run_id
    assert repo.list_turn_task_links(
        principal.tenant_id, session.task_session_id
    )[0].relation == "created"

    state.outcome = TaskOutcome.completed
    state.state_version = 5
    repo.update_task(state, response_payload={"status": "completed"})
    updated = repo.get_detail(principal.tenant_id, conversation.conversation_id)

    assert len(updated.task_sessions) == 1
    assert len(updated.workflow_runs) == 1
    assert updated.task_sessions[0].status == "completed"
    assert updated.task_sessions[0].completed_at is not None
    assert updated.workflow_runs[0].status == "completed"
    assert updated.workflow_runs[0].completed_at is not None


def test_batch_job_links_multiple_independent_task_sessions(tmp_path: Path) -> None:
    repo = ConversationRepository(tmp_path / "conversation.db")
    tenant_id = "tenant_demo"
    conversation, turn = _turn(repo, tenant_id, "同时上架耳机和键盘")
    batch = repo.create_batch_job(
        tenant_id,
        conversation.conversation_id,
        turn.turn_id,
        operation="create_listing",
        item_count=2,
    )

    earphone = repo.create_task_session(
        tenant_id,
        conversation.conversation_id,
        turn.turn_id,
        intent="create_listing",
        title="上架无线耳机",
        batch_job_id=batch.batch_job_id,
        relation="batch_item",
    )
    keyboard = repo.create_task_session(
        tenant_id,
        conversation.conversation_id,
        turn.turn_id,
        intent="create_listing",
        title="上架机械键盘",
        batch_job_id=batch.batch_job_id,
        relation="batch_item",
    )

    refreshed = repo.get_batch_job(tenant_id, batch.batch_job_id)
    detail = repo.get_detail(tenant_id, conversation.conversation_id)
    assert earphone.task_session_id != keyboard.task_session_id
    assert refreshed.item_count == 2
    assert {item.batch_job_id for item in detail.task_sessions} == {batch.batch_job_id}
    assert len(detail.batch_jobs) == 1


def test_task_identity_lookups_are_tenant_scoped(tmp_path: Path) -> None:
    repo = ConversationRepository(tmp_path / "conversation.db")
    conversation, turn = _turn(repo, "tenant_alpha")
    session = repo.create_task_session(
        "tenant_alpha",
        conversation.conversation_id,
        turn.turn_id,
        intent="market_research",
        title="耳机市场调研",
    )

    with pytest.raises(ConversationNotFoundError):
        repo.get_task_session("tenant_beta", session.task_session_id)
