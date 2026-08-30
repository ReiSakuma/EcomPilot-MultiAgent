from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.access.models import default_principal
from app.config import PROJECT_VERSION
from app.conversations.repository import ConversationRepository
from app.copilot.batch_execution import BatchExecutionService
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.main import app
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import TaskOutcome
from app.orchestration.state import TaskState


def main() -> None:
    with TemporaryDirectory() as directory:
        repository = ConversationRepository(Path(directory) / "conversations.db")
        principal = default_principal()
        conversation = repository.create_conversation(principal.tenant_id, title="v46")
        turn = repository.begin_turn(
            principal.tenant_id,
            conversation.conversation_id,
            client_request_id="req_v46_acceptance",
            message="同时上架耳机和键盘",
        ).turn
        batch, records = repository.materialize_batch_plan(
            principal.tenant_id,
            conversation.conversation_id,
            turn.turn_id,
            operation="create_listing",
            items=[
                {"item_id": "item_01", "label": "无线耳机", "structured_request": {}, "status": "ready"},
                {"item_id": "item_02", "label": "机械键盘", "structured_request": {}, "status": "ready"},
            ],
        )
        states: dict[str, TaskState] = {}
        for record in records:
            state = TaskState(
                goal=f"生成{record.label}方案",
                run_id=f"run_v46_{record.item_id}",
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

        calls: list[str] = []

        def approve(task_id, _principal, _version):
            calls.append(task_id)
            if task_id == states["item_02"].task_id:
                raise RuntimeError("acceptance browser failure")
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
                    "approval_required": False,
                    "entity_refs": list(state.entity_refs),
                }
            )

        service = BatchExecutionService(repository, approver=approve)
        first = service.execute(
            batch.batch_job_id, principal=principal, item_ids=["item_01"]
        )
        replay = service.execute(
            batch.batch_job_id, principal=principal, item_ids=["item_01"]
        )
        partial = service.execute(
            batch.batch_job_id, principal=principal, item_ids=["item_02"]
        )
        items = {
            item.item_id: item
            for item in repository.list_batch_items(
                principal.tenant_id, batch.batch_job_id
            )
        }
        route_paths = {route.path for route in app.routes}
        checks = {
            "project_version": PROJECT_VERSION == "0.46.0",
            "selected_item_only": first.executed_count == 1
            and first.remaining_approval_count == 1,
            "idempotent_replay": len(calls) == 2
            and replay.items[0].assistant_message.startswith("该商品此前已经同步"),
            "partial_failure_isolated": partial.status == "partially_completed"
            and items["item_01"].status == "completed"
            and items["item_02"].status == "needs_attention",
            "aggregate_counts": partial.executed_count == 1
            and partial.failed_count == 1,
            "batch_api_exposed": "/api/copilot/batches/{batch_job_id}/approve"
            in route_paths,
        }
    payload = {
        "release": "v46-safe-batch-execution",
        "passed": all(checks.values()),
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
