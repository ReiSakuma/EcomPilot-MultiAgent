from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from app.safety.idempotency import IdempotencyStore
from app.tools.contracts import EmptyInput
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolResultValidationError, ToolSpec, TransientToolError


def run_tool_reliability_eval(
    dataset_path: Path, report_path: Path | None = None
) -> dict[str, Any]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    results = [_run_case(str(case["case_id"]), str(case["expected"])) for case in cases]
    report = {
        "total": len(results),
        "passed": sum(int(result["passed"]) for result in results),
        "pass_rate": round(
            sum(int(result["passed"]) for result in results) / len(results) if results else 0.0,
            4,
        ),
        "case_results": results,
    }
    target = report_path or dataset_path.with_name("v12_tool_reliability_report.json")
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


def _permission_denial() -> str:
    registry = ToolRegistry()
    with registry.agent_scope("listing_agent"):
        registry.call("calculate_margin", price=199, cost=95)
    return "unexpected_success"


def _approval_guard() -> str:
    registry = ToolRegistry()
    plan = {
        "operation": "update_listing",
        "product_id": "reliability_eval",
        "title": "测试商品",
        "price": 199,
        "stock": 10,
    }
    with registry.agent_scope("browser_agent", approved=False):
        registry.call("browser_execute", plan=plan, idempotency_key="eval:approval:blocked")
    return "unexpected_success"


def _parameter_validation() -> str:
    registry = ToolRegistry()
    with registry.agent_scope("strategy_agent"):
        registry.call("calculate_margin", price=-1, cost=95)
    return "unexpected_success"


def _transient_retry() -> str:
    registry = ToolRegistry()
    attempts = 0

    def flaky() -> dict[str, bool]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransientToolError("temporary dependency failure")
        return {"ok": True}

    registry.register(
        ToolSpec(
            name="flaky",
            allowed_agents={"test_agent"},
            input_schema="EmptyInput",
            max_retries=1,
        ),
        flaky,
        EmptyInput,
        lambda result: _require_ok(result),
    )
    with registry.agent_scope("test_agent"):
        registry.call("flaky")
    return f"completed_after_{registry.records()[-1].attempt_count}_attempts"


def _timeout_enforcement() -> str:
    registry = ToolRegistry()

    def slow() -> dict[str, bool]:
        time.sleep(0.03)
        return {"ok": True}

    registry.register(
        ToolSpec(
            name="slow",
            allowed_agents={"test_agent"},
            input_schema="EmptyInput",
            timeout_seconds=0.001,
            max_retries=0,
        ),
        slow,
        EmptyInput,
        lambda result: _require_ok(result),
    )
    with registry.agent_scope("test_agent"):
        registry.call("slow")
    return "unexpected_success"


def _result_validation() -> str:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="malformed", allowed_agents={"test_agent"}, input_schema="EmptyInput"),
        lambda: {"wrong": True},
        EmptyInput,
        lambda result: _require_ok(result),
    )
    with registry.agent_scope("test_agent"):
        registry.call("malformed")
    return "unexpected_success"


def _persistent_idempotency() -> str:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "records.json"
        calls = 0

        def operation() -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"status": "applied"}

        first_replay, _ = IdempotencyStore(path).execute_once(
            "same-key", {"price": 199}, operation
        )
        second_replay, _ = IdempotencyStore(path).execute_once(
            "same-key", {"price": 199}, operation
        )
        if not first_replay and second_replay and calls == 1:
            return "replayed_across_instances"
    return "idempotency_failed"


def _require_ok(result: Any) -> None:
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ToolResultValidationError("result must contain ok=true")


_SCENARIOS = {
    "permission_denial": _permission_denial,
    "approval_guard": _approval_guard,
    "parameter_validation": _parameter_validation,
    "transient_retry": _transient_retry,
    "timeout_enforcement": _timeout_enforcement,
    "result_validation": _result_validation,
    "persistent_idempotency": _persistent_idempotency,
}
