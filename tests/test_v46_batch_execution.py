from __future__ import annotations

from pathlib import Path

import pytest

from app.access.models import AccessPrincipal, default_principal
from app.conversations.repository import (
    ConversationConflictError,
    ConversationNotFoundError,
    ConversationRepository,
)
from app.copilot.batch_execution import BatchExecutionService
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome, PanelDescriptor
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import TaskOutcome
from app.orchestration.state import TaskState


def _prepared_batch(repository: ConversationRepository):
    principal = default_principal()
    conversation = repository.create_conversation(principal.tenant_id, title="v46")
    turn = repository.begin_turn(
        principal.tenant_id,
        conversation.conversation_id,
        client_request_id="req_v46_execution",
        message="同时上架耳机和键盘",
    ).turn
    batch, records = repository.materialize_batch_plan(
        principal.tenant_id,
        conversation.conversation_id,
        turn.turn_id,
        operation="create_listing",
        items=[
            {
                "item_id": "item_01",
                "label": "无线耳机",
                "structured_request": {"category": "无线耳机"},
                "status": "ready",
            },
            {
                "item_id": "item_02",
                "label": "机械键盘",
                "structured_request": {"category": "机械键盘"},
                "status": "ready",
            },
        ],
    )
    states: dict[str, TaskState] = {}
    for record in records:
        state = TaskState(
            goal=f"生成{record.label}方案",
            run_id=f"run_{record.item_id}",
            conversation_id=conversation.conversation_id,
            turn_id=turn.turn_id,
            principal=principal,
            outcome=TaskOutcome.awaiting_approval,
            status="waiting_for_approval",
        )
        CheckpointStore().save(state)
        repository.record_batch_item_state(
            state,
            batch_job_id=batch.batch_job_id,
            item_id=record.item_id,
            task_session_id=record.task_session_id,
            status="awaiting_approval",
        )
        states[record.item_id] = state
    repository.finalize_batch_job(
        principal.tenant_id, batch.batch_job_id, status="awaiting_approval"
    )
    return principal, batch, states


def _successful_response(task_id: str):
    state = CheckpointStore().load(task_id)
    state.outcome = TaskOutcome.completed
    state.status = "completed"
    state.entity_refs = [f"product_{task_id}"]
    CheckpointStore().save(state)
    response = ConversationFacade.build_response(state)
    return response.model_copy(
        update={
            "outcome": CopilotOutcome.completed,
            "store_modified": True,
            "entity_refs": list(state.entity_refs),
            "approval_required": False,
        }
    )


def test_selected_subset_executes_without_touching_unselected_item(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, states = _prepared_batch(repository)
    calls: list[str] = []

    def approve(task_id, _principal, _version):
        calls.append(task_id)
        return _successful_response(task_id)

    report = BatchExecutionService(repository, approver=approve).execute(
        batch.batch_job_id,
        principal=principal,
        item_ids=["item_01"],
        expected_checkpoint_versions={
            "item_01": states["item_01"].checkpoint_version
        },
    )
    items = {
        item.item_id: item
        for item in repository.list_batch_items(principal.tenant_id, batch.batch_job_id)
    }

    assert report.status == "awaiting_approval"
    assert report.executed_count == 1
    assert report.remaining_approval_count == 1
    assert calls == [states["item_01"].task_id]
    assert items["item_01"].status == "completed"
    assert items["item_02"].status == "awaiting_approval"


def test_execution_failure_isolated_after_prior_success(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, states = _prepared_batch(repository)

    def approve(task_id, _principal, _version):
        if task_id == states["item_02"].task_id:
            raise RuntimeError("fixture browser write failed")
        return _successful_response(task_id)

    report = BatchExecutionService(repository, approver=approve).execute(
        batch.batch_job_id,
        principal=principal,
        item_ids=["item_01", "item_02"],
    )
    items = {
        item.item_id: item
        for item in repository.list_batch_items(principal.tenant_id, batch.batch_job_id)
    }

    assert report.status == "partially_completed"
    assert [item.status for item in report.items] == ["completed", "failed"]
    assert items["item_01"].status == "completed"
    assert items["item_02"].status == "needs_attention"
    assert items["item_02"].error_code


def test_completed_item_is_idempotent_on_repeated_confirmation(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, _states = _prepared_batch(repository)
    calls = 0

    def approve(task_id, _principal, _version):
        nonlocal calls
        calls += 1
        return _successful_response(task_id)

    service = BatchExecutionService(repository, approver=approve)
    first = service.execute(
        batch.batch_job_id, principal=principal, item_ids=["item_01"]
    )
    second = service.execute(
        batch.batch_job_id, principal=principal, item_ids=["item_01"]
    )

    assert first.items[0].status == second.items[0].status == "completed"
    assert second.items[0].assistant_message.startswith("该商品此前已经同步")
    assert calls == 1


def test_batch_claim_and_tenant_boundaries_are_enforced(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, _states = _prepared_batch(repository)
    repository.claim_batch_item_execution(
        principal.tenant_id, batch.batch_job_id, "item_01"
    )
    with pytest.raises(ConversationConflictError):
        repository.claim_batch_item_execution(
            principal.tenant_id, batch.batch_job_id, "item_01"
        )

    other = AccessPrincipal(
        subject_id="other",
        tenant_id="tenant_other",
        roles=["operator"],
    )
    with pytest.raises(ConversationNotFoundError):
        BatchExecutionService(repository).execute(
            batch.batch_job_id,
            principal=other,
            item_ids=["item_02"],
        )


def test_execution_updates_durable_conversation_snapshot(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, states = _prepared_batch(repository)
    seed = ConversationFacade.build_response(states["item_01"])
    seed.panels = [panel for panel in seed.panels if panel.panel_id != "requirements"]
    seed.panels.append(
        PanelDescriptor(
            panel_id="requirements",
            title="批次商品方案",
            status="ready",
            summary="2 个商品等待确认",
            data={
                "batch_job_id": batch.batch_job_id,
                "batch_status": "awaiting_approval",
                "items": [
                    {
                        "item_id": item_id,
                        "label": label,
                        "task_id": states[item_id].task_id,
                        "status": "awaiting_approval",
                        "checkpoint_version": states[item_id].checkpoint_version,
                    }
                    for item_id, label in (
                        ("item_01", "无线耳机"),
                        ("item_02", "机械键盘"),
                    )
                ],
            },
            source_agents=["batch_orchestration_service"],
        )
    )
    repository.complete_message_turn(
        principal.tenant_id,
        batch.conversation_id,
        batch.origin_turn_id,
        intent="create_listing",
        assistant_message="2 个商品等待确认",
        response_payload=seed.model_dump(mode="json"),
    )

    report = BatchExecutionService(
        repository, approver=lambda task_id, _principal, _version: _successful_response(task_id)
    ).execute(
        batch.batch_job_id,
        principal=principal,
        item_ids=["item_01"],
    )
    stored = repository.response_for_turn(principal.tenant_id, batch.origin_turn_id)
    payload = stored or {}
    requirement = next(
        panel for panel in payload["panels"] if panel["panel_id"] == "requirements"
    )
    execution = next(
        panel for panel in payload["panels"] if panel["panel_id"] == "execution"
    )
    statuses = {item["item_id"]: item["status"] for item in requirement["data"]["items"]}

    assert report.status == "awaiting_approval"
    assert statuses == {"item_01": "completed", "item_02": "awaiting_approval"}
    assert execution["data"]["batch_execution"]["executed_count"] == 1
    assert repository.get_conversation(
        principal.tenant_id, batch.conversation_id
    ).updated_at
