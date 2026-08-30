from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from app.browser.backends import BrowserBackendError, MockBrowserBackend, _select
from app.eval.badcase import classify_bad_case
from app.eval.metadata import build_run_metadata, write_json_report
from app.eval.protocol_eval import run_model_protocol_case
from app.memory.long_term import LongTermMemory, MerchantMemory, seed_default_merchant_memory
from app.context.manager import ContextManager
from app.orchestration.recovery import RecoveryCoordinator
from app.orchestration.state import TaskState
from app.orchestration.workflow import run_workflow
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.ui import seller_center_editor_html
from app.tools.browser_tools import reset_seller_center


def run_interview_eval(dataset_path: Path, report_path: Path) -> dict[str, Any]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    _validate_dataset(cases)
    results = [_run_case(case) for case in cases]
    passed = sum(int(case["passed"]) for case in results)
    expected_guardrails = [case for case in results if not case["expected_success"]]
    report = {
        "metadata": build_run_metadata(dataset_path=dataset_path, profile="interview_offline"),
        "total": len(results),
        "passed": passed,
        "regression_pass_rate": _rate(passed, len(results)),
        "hard_constraint_satisfaction_rate": _rate(
            sum(int(case["constraint_expectation_passed"]) for case in results), len(results)
        ),
        "expected_guardrail_pass_rate": _rate(
            sum(int(case["passed"]) for case in expected_guardrails),
            len(expected_guardrails),
        ),
        "unauthorized_side_effect_count": sum(
            int(case["unauthorized_side_effect"]) for case in results
        ),
        "category_counts": dict(Counter(case["category"] for case in results)),
        "failure_domain_counts": dict(
            Counter(
                case["failure_domain"]
                for case in results
                if not case["passed"]
            )
        ),
        "latency_ms": {
            "average": round(sum(case["duration_ms"] for case in results) / len(results), 2),
            "p95": _percentile([case["duration_ms"] for case in results], 0.95),
        },
        "case_results": results,
    }
    write_json_report(report_path, report)
    return report


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    runner = case.get("runner", "workflow")
    try:
        if runner == "workflow":
            observed = _run_workflow_case(case)
        elif runner == "model_protocol":
            observed = _run_simple_case(case, run_model_protocol_case(case["scenario"]))
        elif runner == "browser_scenario":
            observed = _run_simple_case(case, _run_browser_scenario(case["scenario"]))
        elif runner == "recovery_scenario":
            observed = _run_recovery_case(case)
        elif runner == "context_scenario":
            observed = _run_simple_case(case, _run_context_scenario(case["scenario"]))
        else:
            raise ValueError(f"Unsupported interview runner: {runner}")
    except Exception as exc:
        observed = {
            "observed": f"{type(exc).__name__}:{exc}",
            "passed": False,
            "constraint_expectation_passed": False,
            "unauthorized_side_effect": False,
            "bad_case": None,
            "run_id": None,
        }
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "failure_domain": case["failure_domain"],
        "expected_success": bool(case["expected_success"]),
        "expected": case.get("expected_status") or case.get("expected_observation"),
        "duration_ms": round((perf_counter() - started) * 1000, 2),
        **observed,
    }


def _run_workflow_case(case: dict[str, Any]) -> dict[str, Any]:
    reset_seller_center()
    state = run_workflow(
        case["goal"],
        approved=bool(case.get("approved", False)),
        approved_by="interview-eval" if case.get("approved") else None,
        approval_reason="frozen interview case" if case.get("approved") else None,
    )
    side_effect_records = [record for record in state.tool_records if record.get("side_effect")]
    unauthorized = bool(side_effect_records and not case.get("approved", False))
    bad_case = None if state.status == "completed" else classify_bad_case(state)
    expected_status = case["expected_status"]
    expected_bad_case = case.get("expected_bad_case")
    expected_violations = set(case.get("expected_constraints", []))
    actual_violations = set(
        state.agent_outputs.get("review_agent", {}).get("violations", [])
    )
    constraint_passed = expected_violations.issubset(actual_violations)
    side_effect_passed = bool(side_effect_records) is bool(case.get("expected_side_effect"))
    bad_case_passed = (
        expected_bad_case is None
        or (bad_case is not None and bad_case.get("case_type") == expected_bad_case)
    )
    passed = (
        state.status == expected_status
        and constraint_passed
        and side_effect_passed
        and bad_case_passed
        and not unauthorized
    )
    return {
        "observed": state.status,
        "passed": passed,
        "constraint_expectation_passed": constraint_passed,
        "side_effect_expectation_passed": side_effect_passed,
        "unauthorized_side_effect": unauthorized,
        "bad_case": bad_case,
        "run_id": state.run_id,
        "model_call_count": len(state.model_records),
        "tool_call_count": len(state.tool_records),
    }


def _run_simple_case(case: dict[str, Any], observation: str) -> dict[str, Any]:
    return {
        "observed": observation,
        "passed": observation == case["expected_observation"],
        "constraint_expectation_passed": True,
        "unauthorized_side_effect": False,
        "bad_case": None,
        "run_id": None,
    }


def _run_browser_scenario(scenario: str) -> str:
    if scenario in {"approval_block", "idempotent_replay", "ticket_integrity"}:
        from app.eval.browser_eval import _run_case as run_browser_case

        expected = {
            "approval_block": "waiting_without_side_effect",
            "idempotent_replay": "single_side_effect",
            "ticket_integrity": "mismatch_and_replay_blocked",
        }[scenario]
        return str(run_browser_case({"case_id": scenario, "expected": expected})["observed"])
    if scenario == "readback_mismatch":
        reset_seller_center()
        backend = MockBrowserBackend()
        plan = _browser_plan("readback_mismatch")
        backend.execute(plan, "interview:readback")
        changed = plan.model_copy(update={"title": "被页面漂移修改的标题"})
        return "mismatch_detected" if not backend.verify(changed)["verified"] else "missed_mismatch"
    if scenario == "uncertain_failure":
        return (
            "uncertain_failure_not_retryable"
            if BrowserBackendError.safe_to_retry is False
            else "unsafe_retry_enabled"
        )
    if scenario == "select_control":
        locator = _RecordingLocator()
        _select(_RecordingPage(locator), [], "operation", "update_listing")
        return "select_option_used" if locator.selected == "update_listing" else "wrong_control_api"
    if scenario == "javascript_escape":
        rendered = seller_center_editor_html("safe-ticket")
        has_escaped_newline = "split('\\n')" in rendered
        has_submit_contract = "JSON.stringify({ticket:executionTicket, plan})" in rendered
        return "rendered_script_contract_valid" if has_escaped_newline and has_submit_contract else "invalid_script_contract"
    raise KeyError(f"Unknown browser scenario: {scenario}")


def _run_recovery_case(case: dict[str, Any]) -> dict[str, Any]:
    from app.eval.recovery_eval import _run_case as run_recovery_case

    result = run_recovery_case(case["scenario"], case["expected_observation"])
    return _run_simple_case(case, str(result["observed"]))


def _run_context_scenario(scenario: str) -> str:
    if scenario == "scoped_memory":
        with tempfile.TemporaryDirectory(prefix="ecompilot_memory_eval_") as directory:
            snippets = seed_default_merchant_memory(
                Path(directory) / "memory.db"
            ).snippets("无线耳机")
        return "scoped_memory_retrieved" if len(snippets) >= 2 else "memory_missing"
    if scenario == "inactive_memory":
        with tempfile.TemporaryDirectory(prefix="ecompilot_memory_eval_") as directory:
            memory = LongTermMemory(Path(directory) / "memory.db")
            memory.add(
                MerchantMemory(
                    scope="global",
                    memory_type="brand_rule",
                    content="expired rule",
                    source="interview-eval",
                    status="inactive",
                )
            )
            snippets = memory.snippets("global")
        return (
            "inactive_memory_excluded"
            if not any("expired rule" in snippet for snippet in snippets)
            else "inactive_memory_leaked"
        )
    if scenario == "context_compression":
        state = TaskState(goal="无线耳机 Context 预算测试")
        state.agent_outputs["market_agent"] = {"competitors": ["x" * 5000]}
        package = ContextManager().build_for_agent("listing_agent", state, token_budget=120)
        return (
            "context_compressed"
            if package.compressed or package.protected_overflow
            else "context_not_compressed"
        )
    raise KeyError(f"Unknown context scenario: {scenario}")


def _browser_plan(product_id: str) -> ExecutionPlan:
    return ExecutionPlan(
        operation="update_listing",
        product_id=product_id,
        title="面试评测无线耳机",
        bullets=["低延迟", "长续航"],
        price=199,
        stock=800,
        coupon=20,
    )


class _RecordingLocator:
    def __init__(self) -> None:
        self.selected: str | None = None

    def select_option(self, value: str) -> None:
        self.selected = value


class _RecordingPage:
    def __init__(self, locator: _RecordingLocator) -> None:
        self.locator = locator

    def get_by_test_id(self, field: str) -> _RecordingLocator:
        assert field == "operation"
        return self.locator


def _validate_dataset(cases: list[dict[str, Any]]) -> None:
    required = {"case_id", "category", "failure_domain", "expected_success", "notes"}
    ids: list[str] = []
    for case in cases:
        missing = required - case.keys()
        if missing:
            raise ValueError(f"Case missing fields {sorted(missing)}: {case.get('case_id')}")
        ids.append(str(case["case_id"]))
    if len(ids) != len(set(ids)):
        raise ValueError("Interview dataset case_id values must be unique")


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator if denominator else 0.0, 4)


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * quantile)))
    return round(ordered[index], 2)
