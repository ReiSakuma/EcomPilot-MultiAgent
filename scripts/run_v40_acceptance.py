from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.access.models import default_principal
from app.config import PROJECT_VERSION
from app.conversations.repository import (
    CONVERSATION_SCHEMA_VERSION,
    ConversationRepository,
)
from app.orchestration.failures import TaskOutcome
from app.orchestration.state import TaskState


def main() -> None:
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="ecompilot-v40-") as directory:
        database_path = Path(directory) / "conversations.db"
        repository = ConversationRepository(database_path)
        principal = default_principal()
        conversation = repository.create_conversation(
            principal.tenant_id, title="v40 task identity acceptance"
        )
        turn = repository.begin_turn(
            principal.tenant_id,
            conversation.conversation_id,
            client_request_id="req_v40_acceptance",
            message="上架一款无线耳机",
        ).turn
        state = TaskState(
            task_id="task_v40_acceptance",
            run_id="run_v40_acceptance",
            goal="上架一款无线耳机",
            conversation_id=conversation.conversation_id,
            turn_id=turn.turn_id,
            principal=principal,
            outcome=TaskOutcome.awaiting_approval,
            state_version=2,
            checkpoint_version=1,
        )
        repository.complete_turn(
            state,
            "方案已经生成，等待确认。",
            response_payload={"outcome": "waiting_for_approval"},
        )
        detail = repository.get_detail(
            principal.tenant_id, conversation.conversation_id
        )
        session = detail.task_sessions[0]
        workflow = detail.workflow_runs[0]
        checks.update(
            {
                "schema_version_8": CONVERSATION_SCHEMA_VERSION == 8,
                "legacy_flow_created_task_session": len(detail.task_sessions) == 1,
                "legacy_flow_created_workflow_run": len(detail.workflow_runs) == 1,
                "workflow_belongs_to_session": (
                    workflow.task_session_id == session.task_session_id
                ),
                "workflow_declares_task_scoped_checkpoint_identity": (
                    workflow.checkpoint_thread_id == session.task_session_id
                ),
            }
        )

        batch_turn = repository.begin_turn(
            principal.tenant_id,
            conversation.conversation_id,
            client_request_id="req_v40_batch_acceptance",
            message="同时上架耳机和键盘",
        ).turn
        batch = repository.create_batch_job(
            principal.tenant_id,
            conversation.conversation_id,
            batch_turn.turn_id,
            operation="create_listing",
            item_count=2,
        )
        for title in ("无线耳机", "机械键盘"):
            repository.create_task_session(
                principal.tenant_id,
                conversation.conversation_id,
                batch_turn.turn_id,
                intent="create_listing",
                title=title,
                batch_job_id=batch.batch_job_id,
                relation="batch_item",
            )
        batch = repository.get_batch_job(principal.tenant_id, batch.batch_job_id)
        checks["batch_has_two_independent_items"] = batch.item_count == 2

        with sqlite3.connect(database_path) as connection:
            checks["migration_recorded"] = (
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                == CONVERSATION_SCHEMA_VERSION
            )

    payload = {
        "version": PROJECT_VERSION,
        "release": "v40-task-identity",
        "passed": all(checks.values()),
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
