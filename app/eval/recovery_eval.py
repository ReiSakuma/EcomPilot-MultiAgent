from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.observability.store import TraceStore
from app.orchestration.recovery import RecoveryCoordinator
from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval
from app.tools.browser_tools import reset_seller_center


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def run_recovery_eval(dataset_path: Path, report_path: Path | None = None) -> dict[str, Any]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    results = [_run_case(str(case["case_id"]), str(case["expected"])) for case in cases]
    passed = sum(int(result["passed"]) for result in results)
    report = {
        "total": len(results),
        "passed": passed,
        "recovery_pass_rate": round(passed / len(results) if results else 0.0, 4),
        "case_results": results,
    }
    target = report_path or dataset_path.with_name("v13_recovery_report.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _run_case(case_id: str, expected: str) -> dict[str, Any]:
    try:
        observed = _SCENARIOS[case_id]()
    except Exception as exc:
        observed = type(exc).__name__
    return {
        "case_id": case_id,
        "expected": expected,
        "observed": observed,
        "passed": observed == expected,
    }


def _approval_resume() -> str:
    reset_seller_center()
    initial = run_workflow(GOAL, approved=False)
    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(approved=True, approver="recovery-eval"),
        expected_checkpoint_version=initial.checkpoint_version,
    )
    detail = TraceStore().get_run(resumed.run_id)
    started = [
        event.get("component_name")
        for event in detail["events"]
        if event.get("event_type") == "node_started"
    ]
    if resumed.status == "completed" and started == ["browser_agent"]:
        return "completed_browser_only"
    return "approval_resume_failed"


def _side_effect_retry() -> str:
    reset_seller_center()
    failed = run_workflow(f"{GOAL}模拟执行验证失败", approved=True)
    resumed = resume_workflow(
        failed.task_id,
        retry_node="browser",
        constraint_updates={"force_execution_verification_failure": False},
        requested_by="recovery-eval",
    )
    execute_records = [
        record for record in resumed.tool_records if record["tool_name"] == "browser_execute"
    ]
    if (
        resumed.status == "completed"
        and len(execute_records) == 2
        and execute_records[-1]["idempotent_replay"]
    ):
        return "completed_with_idempotent_replay"
    return "side_effect_retry_failed"


def _stale_checkpoint() -> str:
    state = run_workflow(GOAL, approved=False)
    resume_workflow(
        state.task_id,
        approval=Approval(approved=True, approver="recovery-eval"),
        expected_checkpoint_version=max(0, state.checkpoint_version - 1),
    )
    return "unexpected_success"


def _constraint_replan() -> str:
    reset_seller_center()
    failed = run_workflow(GOAL.replace("成本 95", "成本 180"), approved=True)
    resumed = resume_workflow(
        failed.task_id,
        retry_node="strategy",
        constraint_updates={"cost": 95},
        requested_by="recovery-eval",
    )
    detail = TraceStore().get_run(resumed.run_id)
    started = [
        event.get("component_name")
        for event in detail["events"]
        if event.get("event_type") == "node_started"
    ]
    if (
        resumed.status == "completed"
        and set(started) == {
            "market_price_gate_agent",
            "listing_agent",
            "strategy_agent",
            "review_agent",
            "browser_agent",
        }
    ):
        return "completed_from_strategy"
    return "constraint_replan_failed"


def _completed_task_rejected() -> str:
    reset_seller_center()
    state = run_workflow(GOAL, approved=True)
    resume_workflow(state.task_id, retry_node="browser")
    return "unexpected_success"


def _invalid_constraint_patch() -> str:
    state = run_workflow(GOAL, approved=False)
    resume_workflow(
        state.task_id,
        approval=Approval(approved=True, approver="recovery-eval"),
        constraint_updates={"inventory": -1},
    )
    return "unexpected_success"


def _concurrent_resume_guard() -> str:
    task_id = "task_recovery_lock_eval"
    with RecoveryCoordinator.claim(task_id):
        with RecoveryCoordinator.claim(task_id):
            return "unexpected_success"


_SCENARIOS = {
    "approval_resume": _approval_resume,
    "side_effect_retry": _side_effect_retry,
    "constraint_replan": _constraint_replan,
    "stale_checkpoint": _stale_checkpoint,
    "completed_task_rejected": _completed_task_rejected,
    "invalid_constraint_patch": _invalid_constraint_patch,
    "concurrent_resume_guard": _concurrent_resume_guard,
}
