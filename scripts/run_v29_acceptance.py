from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.access.models import default_principal
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.facade import ConversationFacade
from app.model.adapter import ModelAdapter
from app.orchestration.checkpoint import CheckpointStore


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "summaries"
FULL_LISTING = (
    "我要上架一款成本95元的无线耳机，目标售价300元，主要面向游戏爱好者，"
    "库存800件，毛利率不能低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认的产品形态：未确认。"
)


def main() -> None:
    principal = default_principal()
    compiler = RequestCompiler(ModelAdapter(provider="deterministic", model="local-rule-v6"))
    dataset = json.loads((ROOT / "data/eval/v29_intents.json").read_text(encoding="utf-8"))
    correct = sum(
        compiler.compile(case["text"]).decision.intent.value == case["intent"]
        for case in dataset
    )

    with tempfile.TemporaryDirectory(prefix="ecompilot_v29_") as temporary:
        repository = ConversationRepository(Path(temporary) / "conversations.db")
        facade = ConversationFacade(repository=repository)

        waiting = facade.handle_message(
            "我要上架一款成本95元的无线耳机",
            principal=principal,
            client_request_id="req_v29_waiting",
        )
        duplicate = facade.handle_message(
            "我要上架一款成本95元的无线耳机",
            principal=principal,
            conversation_id=waiting.conversation_id,
            client_request_id="req_v29_waiting",
        )
        resumed = facade.handle_message(
            "售价300元，库存800件，毛利率不低于40%",
            principal=principal,
            conversation_id=waiting.conversation_id,
            client_request_id="req_v29_resume",
        )
        market = facade.handle_message(
            "我想了解无线耳机最近30天的整体价格区间和竞品情况",
            principal=principal,
            client_request_id="req_v29_market",
        )
        market_state = CheckpointStore().load(market.task_id)
        chat = facade.handle_message(
            "清华和哈工大哪个更好",
            principal=principal,
            client_request_id="req_v29_chat",
        )

        checks = {
            "intent_eval_at_least_95_percent": correct / len(dataset) >= 0.95,
            "missing_fields_wait_for_input": waiting.outcome.value == "waiting_for_input" and waiting.task_id is None,
            "waiting_response_is_idempotent": duplicate.response_id == waiting.response_id,
            "same_thread_resumes_to_listing": resumed.conversation_id == waiting.conversation_id and resumed.outcome.value == "awaiting_approval",
            "market_is_read_only": market.outcome.value == "read_only_completed" and not market.approval_required and not market.store_modified,
            "market_has_only_market_node": set(market_state.nodes) == {"market"},
            "market_has_no_write_tools": not any(record.get("risk_level") in {"medium", "high"} for record in market_state.tool_records),
            "general_chat_has_no_task": chat.task_id is None and chat.action_summary.tool_call_count == 0,
            "response_exposes_intent_and_scope": market.intent is not None and bool(market.data_scope),
            "listing_workflow_preserved": resumed.task_id is not None,
        }
        report = {
            "version": "v29",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": all(checks.values()),
            "intent_accuracy": correct / len(dataset),
            "checks": checks,
            "waiting_conversation_id": waiting.conversation_id,
            "resumed_task_id": resumed.task_id,
            "market_task_id": market.task_id,
        }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "V29_ACCEPTANCE.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = "\n".join(
        f"| {name} | {'passed' if passed else 'failed'} |"
        for name, passed in checks.items()
    )
    (REPORT_DIR / "V29_ACCEPTANCE.md").write_text(
        "# V29 Acceptance\n\n"
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
