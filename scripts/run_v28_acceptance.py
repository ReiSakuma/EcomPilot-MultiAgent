from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.access.models import default_principal
from app.config import THREAD_CHECKPOINT_DATABASE_PATH
from app.conversations.repository import ConversationNotFoundError, ConversationRepository
from app.copilot.facade import ConversationFacade
from app.copilot.parity import run_graph_parity
from app.copilot_ui import COPILOT_HTML


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价300元，主要面向游戏爱好者，"
    "库存800件，毛利率不能低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认的产品形态：未确认。运营目标：主打性价比。"
)
ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "summaries"


def main() -> None:
    principal = default_principal()
    with tempfile.TemporaryDirectory(prefix="ecompilot_v28_") as temporary:
        database = Path(temporary) / "conversations.db"
        repository = ConversationRepository(database)
        facade = ConversationFacade(repository=repository)
        request_id = "req_v28_acceptance"
        first = facade.handle_message(
            GOAL,
            principal=principal,
            client_request_id=request_id,
        )
        duplicate = facade.handle_message(
            GOAL,
            principal=principal,
            conversation_id=first.conversation_id,
            client_request_id=request_id,
        )
        completed = facade.approve(first.task_id, principal=principal)
        reopened = ConversationRepository(database)
        detail = reopened.get_detail(principal.tenant_id, first.conversation_id)
        hidden_from_other_tenant = False
        try:
            reopened.get_detail("tenant_beta", first.conversation_id)
        except ConversationNotFoundError:
            hidden_from_other_tenant = True

        parity = run_graph_parity(GOAL, principal=principal)
        with sqlite3.connect(THREAD_CHECKPOINT_DATABASE_PATH) as connection:
            thread_checkpoint_count = connection.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                (first.conversation_id,),
            ).fetchone()[0]

        checks = {
            "conversation_protocol_v1_1": first.protocol_version == "1.1",
            "stable_conversation_and_turn_ids": bool(first.conversation_id and first.turn_id),
            "awaiting_approval": first.outcome.value == "awaiting_approval",
            "client_request_id_idempotency": duplicate.task_id == first.task_id,
            "approval_executes_same_task": completed.task_id == first.task_id,
            "store_verified_after_approval": completed.store_modified is True,
            "restart_repository_restores_messages": len(detail.messages) == 3,
            "task_index_restored": detail.tasks[0].outcome == "completed",
            "tenant_isolation": hidden_from_other_tenant,
            "langgraph_thread_persisted": thread_checkpoint_count >= 3,
            "graph_parity": parity.passed,
            "history_ui_is_data_backed": all(
                marker in COPILOT_HTML
                for marker in (
                    "/api/copilot/conversations",
                    "loadConversations",
                    "openConversation",
                    "client_request_id",
                )
            ),
            "legacy_surfaces_linked": all(
                route in COPILOT_HTML for route in ("/ops", "/traces", "/seller-center")
            ),
        }
        report = {
            "version": "v28",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": all(checks.values()),
            "checks": checks,
            "conversation_id": first.conversation_id,
            "turn_id": first.turn_id,
            "task_id": first.task_id,
            "thread_checkpoint_count": thread_checkpoint_count,
            "message_count_after_approval": len(detail.messages),
            "graph_parity": parity.model_dump(mode="json"),
            "limitations": [
                "Every message still uses the create-listing workflow.",
                "Intent routing and clarification begin in V29.",
                "SQLite is the local MVP store; production scale requires PostgreSQL.",
            ],
        }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "V28_ACCEPTANCE.json"
    markdown_path = REPORT_DIR / "V28_ACCEPTANCE.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = "\n".join(
        f"| {name} | {'passed' if passed else 'failed'} |"
        for name, passed in checks.items()
    )
    markdown_path.write_text(
        "# V28 Acceptance\n\n"
        f"Overall: **{'passed' if report['passed'] else 'failed'}**\n\n"
        "| Check | Result |\n|---|---|\n"
        f"{rows}\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
