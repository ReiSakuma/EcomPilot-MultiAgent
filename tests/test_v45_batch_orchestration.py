from __future__ import annotations

from pathlib import Path

from app.access.models import default_principal
from app.conversations.repository import ConversationRepository
from app.copilot.batch import BoundedBatchOrchestrator
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import TaskOutcome
from app.orchestration.state import TaskState


MESSAGE = (
    "帮我同时上架无线耳机和机械键盘。"
    "耳机成本95元售价300元，键盘成本120元售价260元，"
    "库存都是800件，最低毛利率都不低于20%。"
)


def _batch(repository: ConversationRepository):
    conversation = repository.create_conversation("tenant_demo", title="batch")
    turn = repository.begin_turn(
        "tenant_demo",
        conversation.conversation_id,
        client_request_id="req_v45_batch_unit",
        message=MESSAGE,
    ).turn
    batch, items = repository.materialize_batch_plan(
        "tenant_demo",
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
    return conversation, turn, batch, items


def _state(conversation_id: str, turn_id: str, label: str) -> TaskState:
    state = TaskState(
        goal=f"生成{label}方案",
        run_id=f"run_{label}",
        conversation_id=conversation_id,
        turn_id=turn_id,
        principal=default_principal(),
        outcome=TaskOutcome.awaiting_approval,
        status="waiting_for_approval",
    )
    CheckpointStore().save(state)
    return state


def test_one_failed_item_does_not_erase_a_successful_sibling(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation, turn, batch, _items = _batch(repository)

    def runner(item):
        if item.label == "机械键盘":
            raise RuntimeError("fixture child failure")
        return _state(conversation.conversation_id, turn.turn_id, item.label)

    report = BoundedBatchOrchestrator(repository).run(
        "tenant_demo", batch.batch_job_id, runner
    )
    persisted = repository.get_batch_job("tenant_demo", batch.batch_job_id)
    item_statuses = {
        item.label: item.status
        for item in repository.list_batch_items("tenant_demo", batch.batch_job_id)
    }

    assert report.status == "partially_completed"
    assert report.successful_count == 1
    assert report.failed_count == 1
    assert item_statuses == {"无线耳机": "awaiting_approval", "机械键盘": "failed"}
    assert persisted.completed_count == 1
    assert persisted.failed_count == 1


def test_replaying_a_successful_batch_reuses_child_tasks(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation, turn, batch, _items = _batch(repository)
    calls = 0

    def runner(item):
        nonlocal calls
        calls += 1
        return _state(conversation.conversation_id, turn.turn_id, item.label)

    orchestrator = BoundedBatchOrchestrator(repository)
    first = orchestrator.run("tenant_demo", batch.batch_job_id, runner)
    second = orchestrator.run("tenant_demo", batch.batch_job_id, runner)

    assert first.successful_count == second.successful_count == 2
    assert calls == 2
    assert [item.task_id for item in first.items] == [item.task_id for item in second.items]


def test_confirmation_runs_two_independent_child_workflows(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    facade = ConversationFacade(repository=repository)
    first = facade.handle_message(
        MESSAGE,
        principal=default_principal(),
        client_request_id="req_v45_batch_start",
    )
    second = facade.handle_message(
        "确认批次",
        principal=default_principal(),
        conversation_id=first.conversation_id,
        client_request_id="req_v45_batch_confirm",
    )
    detail = repository.get_detail("tenant_demo", first.conversation_id)

    assert first.outcome is CopilotOutcome.waiting_for_input
    assert second.outcome is CopilotOutcome.awaiting_approval
    assert second.task_id is None
    assert second.approval_required is False
    assert detail.batch_jobs[0].status == "awaiting_approval"
    assert detail.batch_jobs[0].completed_count == 2
    assert len({item.task_id for item in detail.batch_items}) == 2
    assert all(item.status == "awaiting_approval" for item in detail.batch_items)
