from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langgraph.checkpoint.sqlite import SqliteSaver

from app.access.models import default_principal
from app.config import PROJECT_VERSION
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.graph import V33ConversationGraph
from app.model.adapter import ModelAdapter


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = ConversationRepository(root / "conversations.db")
        conversation = repo.create_conversation("tenant_demo", title="多任务验收")
        connection = sqlite3.connect(root / "threads.db", check_same_thread=False)
        saver = SqliteSaver(connection)
        saver.setup()
        graph = V33ConversationGraph(
            compiler=RequestCompiler(ModelAdapter(provider="deterministic", model="local-rule-v6")),
            repository=repo,
            checkpointer=saver,
        )
        first, _, _ = graph.invoke(
            "帮我上架无线耳机。", principal=default_principal(),
            conversation_id=conversation.conversation_id, turn_id="turn_accept_a",
            thread_id="session_accept_a",
        )
        second, _, _ = graph.invoke(
            "帮我上架机械键盘。", principal=default_principal(),
            conversation_id=conversation.conversation_id, turn_id="turn_accept_b",
            thread_id="session_accept_b",
        )
        before_b = connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id='session_accept_b'"
        ).fetchone()[0]
        resumed, _, compiled = graph.resume(
            "成本95元，库存800件。", conversation_id=conversation.conversation_id,
            turn_id="turn_accept_a_2", thread_id="session_accept_a",
        )
        after_b = connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id='session_accept_b'"
        ).fetchone()[0]
        checks = {
            "project_version": PROJECT_VERSION == "0.43.0",
            "distinct_response_threads": first.thread_id != second.thread_id,
            "task_a_context_restored": compiled.structured_request["category"] == "无线耳机",
            "task_b_checkpoint_unchanged": before_b == after_b and before_b > 0,
            "resume_reports_task_thread": resumed.thread_id == "session_accept_a",
        }
        connection.close()
    result = {"release": "v43-task-scoped-checkpoints", "passed": all(checks.values()), "checks": checks}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
