from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from app.browser.runtime import get_browser_runtime_status
from app.browser.service import execute_ticketed_plan
from app.browser.tickets import (
    BrowserTicketConsumedError,
    BrowserTicketMismatchError,
    BrowserTicketStore,
)
from app.config import DATA_DIR
from app.orchestration.workflow import run_workflow
from app.seller_center.schemas import ExecutionPlan
from app.tools.browser_tools import browser_execute, get_seller_center_snapshot, reset_seller_center


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def run_browser_eval(
    dataset_path: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    results = [_run_case(item) for item in cases]
    passed = sum(int(item["passed"]) for item in results)
    report = {
        "runtime": get_browser_runtime_status(),
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results) if results else 0.0, 4),
        "case_results": results,
    }
    target = report_path or DATA_DIR / "eval" / "v15_browser_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _run_case(item: dict[str, Any]) -> dict[str, Any]:
    case_id = item["case_id"]
    expected = item["expected"]
    real_browser = bool(get_browser_runtime_status()["real_browser_enabled"])
    observed = "unexpected"
    try:
        if case_id == "approved_execution":
            _reset(real_browser)
            if real_browser:
                state_data = _api_json(
                    "/tasks/run",
                    method="POST",
                    payload={
                        "goal": GOAL,
                        "approval": {
                            "approved": True,
                            "approver": "browser-eval",
                            "reason": "real browser evaluation",
                        },
                    },
                )
                state_status = state_data["status"]
                output = state_data.get("agent_outputs", {}).get("browser_agent", {})
            else:
                state = run_workflow(GOAL, approved=True, approved_by="browser-eval")
                state_status = state.status
                output = state.agent_outputs.get("browser_agent", {})
            execution = output.get("browser_result", {})
            verification = output.get("verification", {})
            if (
                state_status == "completed"
                and execution.get("backend") == get_browser_runtime_status()["backend"]
                and verification.get("verified")
                and execution.get("actions")
            ):
                observed = "verified_backend_execution"
        elif case_id == "approval_block":
            _reset(real_browser)
            if real_browser:
                state_data = _api_json(
                    "/tasks/run",
                    method="POST",
                    payload={"goal": GOAL, "approval": {"approved": False}},
                )
                state_status = state_data["status"]
                side_effects = [
                    record
                    for record in state_data.get("tool_records", [])
                    if record.get("side_effect")
                ]
                snapshot = _api_json("/seller-center/state")
            else:
                state = run_workflow(GOAL, approved=False)
                state_status = state.status
                side_effects = [
                    record for record in state.tool_records if record.get("side_effect")
                ]
                snapshot = get_seller_center_snapshot()
            if (
                state_status == "waiting_for_approval"
                and not side_effects
                and not snapshot["products"]
            ):
                observed = "waiting_without_side_effect"
        elif case_id == "idempotent_replay":
            _reset(real_browser)
            plan = _plan("idem_product")
            if real_browser:
                request_payload = {
                    "plan": plan.model_dump(mode="json"),
                    "idempotency_key": "v15:idem:product",
                    "approval": {
                        "approved": True,
                        "approver": "browser-eval",
                        "reason": "idempotency evaluation",
                    },
                }
                first = _api_json(
                    "/seller-center/execute", method="POST", payload=request_payload
                )
                second = _api_json(
                    "/seller-center/execute", method="POST", payload=request_payload
                )
            else:
                first = browser_execute(plan.model_dump(mode="json"), "v15:idem:product")
                second = browser_execute(plan.model_dump(mode="json"), "v15:idem:product")
            if not first["idempotent_replay"] and second["idempotent_replay"]:
                observed = "single_side_effect"
        elif case_id == "ticket_integrity":
            reset_seller_center()
            plan = _plan("ticket_product")
            ticket = BrowserTicketStore.issue(plan.model_dump(mode="json"))
            mismatch_blocked = False
            replay_blocked = False
            changed = plan.model_copy(update={"price": 299.0})
            try:
                execute_ticketed_plan(ticket, changed)
            except BrowserTicketMismatchError:
                mismatch_blocked = True
            execute_ticketed_plan(ticket, plan)
            try:
                execute_ticketed_plan(ticket, plan)
            except BrowserTicketConsumedError:
                replay_blocked = True
            if mismatch_blocked and replay_blocked:
                observed = "mismatch_and_replay_blocked"
    except Exception as exc:
        observed = f"{type(exc).__name__}:{exc}"
    return {
        "case_id": case_id,
        "expected": expected,
        "observed": observed,
        "passed": observed == expected,
    }


def _reset(real_browser: bool) -> None:
    if real_browser:
        _api_json("/seller-center/reset", method="POST")
    else:
        reset_seller_center()


def _api_json(
    path: str, *, method: str = "GET", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{get_browser_runtime_status()['base_url']}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise TypeError(f"Expected JSON object from {path}")
    return body


def _plan(product_id: str) -> ExecutionPlan:
    return ExecutionPlan(
        operation="update_listing",
        product_id=product_id,
        title="V15 浏览器测试无线耳机",
        bullets=["低延迟", "长续航"],
        price=199,
        stock=800,
        coupon=20,
    )
