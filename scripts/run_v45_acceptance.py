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
from app.copilot.batch import BoundedBatchOrchestrator
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import TaskOutcome
from app.orchestration.state import TaskState


def main() -> None:
    with TemporaryDirectory() as directory:
        repository = ConversationRepository(Path(directory) / "conversations.db")
        conversation = repository.create_conversation("tenant_demo", title="v45")
        turn = repository.begin_turn(
            "tenant_demo", conversation.conversation_id,
            client_request_id="req_v45_acceptance",
            message="同时上架耳机和键盘",
        ).turn
        batch, _ = repository.materialize_batch_plan(
            "tenant_demo", conversation.conversation_id, turn.turn_id,
            operation="create_listing",
            items=[
                {"item_id": "item_01", "label": "耳机", "structured_request": {}, "status": "ready"},
                {"item_id": "item_02", "label": "键盘", "structured_request": {}, "status": "ready"},
            ],
        )

        def runner(item):
            if item.label == "键盘":
                raise RuntimeError("acceptance child failure")
            state = TaskState(
                goal="生成耳机方案",
                run_id="run_v45_acceptance",
                conversation_id=conversation.conversation_id,
                turn_id=turn.turn_id,
                principal=default_principal(),
                outcome=TaskOutcome.awaiting_approval,
            )
            CheckpointStore().save(state)
            return state

        report = BoundedBatchOrchestrator(repository).run(
            "tenant_demo", batch.batch_job_id, runner
        )
        persisted = repository.get_batch_job("tenant_demo", batch.batch_job_id)
        items = repository.list_batch_items("tenant_demo", batch.batch_job_id)
        checks = {
            "project_version": PROJECT_VERSION == "0.45.0",
            "partial_failure_isolated": report.status == "partially_completed",
            "successful_item_retained": any(item.status == "awaiting_approval" for item in items),
            "failed_item_recorded": any(item.status == "failed" and item.error_code for item in items),
            "aggregate_counts": persisted.completed_count == 1 and persisted.failed_count == 1,
        }
    payload = {"release": "v45-bounded-batch", "passed": all(checks.values()), "checks": checks}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
