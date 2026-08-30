from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.access.models import default_principal
from app.agents.supervisor import Supervisor
from app.config import PROJECT_ROOT, PROJECT_VERSION


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 300 元，库存 800 件，"
    "主要面向游戏爱好者，毛利率不能低于 40%。"
    "已确认的产品功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
    "已确认的产品形态：未确认。"
)


def main() -> int:
    waiting = Supervisor().run(
        GOAL,
        principal=default_principal(),
        conversation_id="conv_v54_acceptance",
        turn_id="turn_v54_acceptance_1",
    )
    assessment = waiting.agent_outputs.get("market_price_gate_agent", {})
    market_before = waiting.agent_outputs.get("market_agent")
    market_calls_before = sum(
        record.get("tool_name") == "build_market_report"
        for record in waiting.tool_records
    )
    checks = {
        "price_deviation_waits_for_user": waiting.status == "waiting_for_input",
        "gate_uses_core_reference": bool(assessment.get("core_reference_price")),
        "high_price_detected": assessment.get("position") == "above_market",
        "listing_not_started_before_confirmation": waiting.nodes["listing"].status.value
        == "pending",
        "strategy_not_started_before_confirmation": waiting.nodes["strategy"].status.value
        == "pending",
        "only_market_tool_called_before_confirmation": [
            record.get("tool_name") for record in waiting.tool_records
        ]
        == ["build_market_report"],
    }

    resumed = Supervisor().resume(
        waiting.task_id,
        constraint_updates={
            "pricing_confirmation_action": "adopt_suggested_price",
            "target_price": 189,
            "price_confirmation_request_id": "price_confirm_v54_acceptance",
        },
        expected_checkpoint_version=waiting.checkpoint_version,
        requested_by="v54-acceptance",
        reason="accept suggested price",
        turn_id="turn_v54_acceptance_2",
    )
    checks.update(
        {
            "resume_creates_child_run": (
                resumed.parent_run_id == waiting.run_id
                and resumed.run_id != waiting.run_id
            ),
            "market_evidence_reused": resumed.agent_outputs.get("market_agent")
            == market_before,
            "market_tool_not_repeated": sum(
                record.get("tool_name") == "build_market_report"
                for record in resumed.tool_records
            )
            == market_calls_before
            == 1,
            "adopted_price_reaches_strategy": resumed.agent_outputs.get(
                "strategy_agent", {}
            ).get("price")
            == 189,
            "workflow_reaches_approval": resumed.status == "waiting_for_approval",
            "store_not_modified_before_approval": (
                not resumed.agent_outputs.get("browser_agent", {}).get("browser_result")
                and not any(
                    record.get("tool_name") == "browser_execute"
                    for record in resumed.tool_records
                )
            ),
        }
    )
    report = {
        "version": "v54",
        "project_version": PROJECT_VERSION,
        "passed": all(checks.values()),
        "checks": checks,
        "waiting_run": {
            "task_id": waiting.task_id,
            "run_id": waiting.run_id,
            "checkpoint_version": waiting.checkpoint_version,
            "assessment": assessment,
        },
        "resumed_run": {
            "task_id": resumed.task_id,
            "run_id": resumed.run_id,
            "parent_run_id": resumed.parent_run_id,
            "checkpoint_version": resumed.checkpoint_version,
            "status": resumed.status,
        },
        "boundary": (
            "v54 implements deterministic market-price gating and checkpoint recovery. "
            "Dedicated pricing action controls and operations evidence UI belong to v55."
        ),
    }
    target = PROJECT_ROOT / "reports" / "v54" / "v54_acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
