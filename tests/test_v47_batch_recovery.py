from __future__ import annotations

from pathlib import Path

from app.access.models import default_principal
from app.conversations.repository import ConversationRepository
from app.copilot.batch_execution import BatchExecutionService
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.main import app
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import TaskOutcome
from app.orchestration.state import TaskState


def _batch(repository: ConversationRepository):
    principal = default_principal()
    conversation = repository.create_conversation(principal.tenant_id, title="v47")
    turn = repository.begin_turn(
        principal.tenant_id,
        conversation.conversation_id,
        client_request_id="req_v47_recovery",
        message="上架耳机",
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
    record = records[0]
    state = TaskState(
        goal="生成无线耳机方案",
        run_id="run_v47_item_01",
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
    repository.finalize_batch_job(
        principal.tenant_id, batch.batch_job_id, status="awaiting_approval"
    )
    return principal, batch, state


def _success(task_id: str):
    state = CheckpointStore().load(task_id)
    state.status = "completed"
    state.outcome = TaskOutcome.completed
    state.entity_refs = [f"product_{task_id}"]
    CheckpointStore().save(state)
    response = ConversationFacade.build_response(state)
    return response.model_copy(
        update={
            "outcome": CopilotOutcome.completed,
            "store_modified": True,
            "approval_required": False,
            "entity_refs": list(state.entity_refs),
        }
    )


def test_failed_child_can_be_retried_without_losing_attempt_audit(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, state = _batch(repository)
    calls = 0

    def approve(task_id, _principal, _version):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary seller center outage")
        return _success(task_id)

    service = BatchExecutionService(repository, approver=approve)
    failed = service.execute(
        batch.batch_job_id, principal=principal, item_ids=["item_01"]
    )
    recovered = service.execute(
        batch.batch_job_id,
        principal=principal,
        item_ids=["item_01"],
        retry_failed=True,
    )
    item = repository.list_batch_items(principal.tenant_id, batch.batch_job_id)[0]

    assert failed.items[0].status == "failed"
    assert recovered.items[0].status == "completed"
    assert calls == 2
    assert item.status == "completed"
    assert item.execution_attempts == 2
    assert [entry["status"] for entry in item.execution_history] == [
        "needs_attention",
        "completed",
    ]
    assert CheckpointStore().load(state.task_id).entity_refs


def test_unknown_write_state_requires_reconciliation_before_retry(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, state = _batch(repository)
    calls = 0

    def approve(_task_id, _principal, _version):
        nonlocal calls
        calls += 1
        raise RuntimeError("browser disconnected after submit")

    service = BatchExecutionService(repository, approver=approve)
    service.execute(batch.batch_job_id, principal=principal, item_ids=["item_01"])
    checkpoint = CheckpointStore().load(state.task_id)
    checkpoint.tool_records.append(
        {"tool_name": "browser_execute", "status": "unknown", "side_effect": True}
    )
    CheckpointStore().save(checkpoint)

    report = service.execute(
        batch.batch_job_id,
        principal=principal,
        item_ids=["item_01"],
        retry_failed=True,
    )
    item = repository.list_batch_items(principal.tenant_id, batch.batch_job_id)[0]

    assert calls == 1
    assert report.items[0].error_code == "batch_unknown_write_requires_reconciliation"
    assert item.status == "needs_attention"
    assert item.execution_attempts == 1


def test_retry_stops_after_three_execution_attempts(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    principal, batch, _state = _batch(repository)
    calls = 0

    def always_fail(_task_id, _principal, _version):
        nonlocal calls
        calls += 1
        raise RuntimeError("persistent browser failure")

    service = BatchExecutionService(repository, approver=always_fail)
    service.execute(batch.batch_job_id, principal=principal, item_ids=["item_01"])
    for _ in range(2):
        service.execute(
            batch.batch_job_id,
            principal=principal,
            item_ids=["item_01"],
            retry_failed=True,
        )
    exhausted = service.execute(
        batch.batch_job_id,
        principal=principal,
        item_ids=["item_01"],
        retry_failed=True,
    )
    item = repository.list_batch_items(principal.tenant_id, batch.batch_job_id)[0]

    assert calls == 3
    assert item.execution_attempts == 3
    assert len(item.execution_history) == 3
    assert exhausted.items[0].error_code == "batch_retry_exhausted"


def test_v47_retry_api_is_exposed() -> None:
    assert "/api/copilot/batches/{batch_job_id}/retry" in {
        route.path for route in app.routes
    }
