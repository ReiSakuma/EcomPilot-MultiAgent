from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.context.budget import ContextBudgetManager  # noqa: E402
from app.context.schemas import BudgetedContextItem  # noqa: E402
from app.access.identity import resolve_principal  # noqa: E402
from app.conversations.repository import ConversationRepository  # noqa: E402
from app.copilot.compiler import RequestCompiler  # noqa: E402
from app.copilot.facade import ConversationFacade  # noqa: E402
from app.copilot.intents import RequestMode  # noqa: E402
from app.copilot.multi_intent import MultiIntentExecutor  # noqa: E402
from app.copilot.routing import ConversationOrchestrator  # noqa: E402
from app.model.adapter import ModelAdapter  # noqa: E402


def main() -> None:
    compiler = RequestCompiler(ModelAdapter("deterministic", "local-rule-v6"))
    dependent = compiler.compile(
        "先查询无线耳机市场价格区间，然后上架一款成本95元、售价300元、库存800件的无线耳机"
    )
    dependent_plan = ConversationOrchestrator().plan(dependent)
    parallel = compiler.compile("查询无线耳机市场行情；另外查询 task_abcdef12 的任务状态")
    parallel_plan = ConversationOrchestrator().plan(parallel)
    execution = MultiIntentExecutor().execute(
        parallel_plan,
        lambda unit, _deps: {"intent": unit.intent.value, "artifact_refs": [unit.intent_id]},
    )
    conflict = compiler.compile(
        "上架成本95元售价230元库存800件的无线耳机；另外上架成本95元售价250元库存800件的无线耳机"
    )
    budget = ContextBudgetManager(context_window_tokens=512).decide([
        BudgetedContextItem(item_id="authority", priority="P0", content="tenant permission"),
        BudgetedContextItem(item_id="constraints", priority="P1", content={"cost": 95}),
        BudgetedContextItem(item_id="history", priority="P3", content="x" * 1000),
        BudgetedContextItem(item_id="debug", priority="P4", content="y" * 1000),
    ], next_input="z" * 500)
    with tempfile.TemporaryDirectory(prefix="ecompilot-v37-context-") as directory:
        graph_response = ConversationFacade(
            repository=ConversationRepository(Path(directory) / "conversation.db")
        ).handle_message(
            "查询无线耳机市场行情；另外查询蓝牙音箱市场价格区间",
            principal=resolve_principal(None),
            client_request_id="req_context_acceptance_1234",
        )
    scenarios = {
        "dependent_intent_dag": dependent.intent_units[1].dependencies
        == [dependent.intent_units[0].intent_id],
        "independent_reads_parallel": parallel_plan.execution_groups[0].execution == "parallel",
        "bounded_executor_completed": execution.status == "completed" and len(execution.results) == 2,
        "main_graph_aggregates_parallel_reads": (
            graph_response.outcome.value == "read_only_completed"
            and graph_response.action_summary.headline == "已处理 2 个只读意图"
        ),
        "conflicting_writes_clarify": conflict.assessment.mode is RequestMode.clarify,
        "protected_context_retained": {"authority", "constraints"}
        <= {item.item_id for item in budget.selected},
        "debug_context_dropped": "debug" in budget.dropped_item_ids,
        "output_reserve_at_least_30_percent": budget.reserved_tokens >= int(512 * 0.30),
    }
    report = {
        "version": "v37-multi-intent-context",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(scenarios.values()),
        "scenarios": scenarios,
        "intent_units": [item.model_dump(mode="json") for item in dependent.intent_units],
        "execution_groups": [item.model_dump(mode="json") for item in dependent_plan.execution_groups],
        "context_budget": budget.model_dump(mode="json"),
    }
    output = ROOT / "reports" / "raw" / "v37_context_acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
