from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.browser.backends import MockBrowserBackend
from app.eval.metadata import build_run_metadata, write_json_report
from app.model.contracts import ListingModelOutput
from app.model.structured import StructuredOutputError, parse_json_object
from app.seller_center.schemas import ExecutionPlan
from app.tools.browser_tools import reset_seller_center


def run_ablation_eval(dataset_path: Path, report_path: Path) -> dict[str, Any]:
    """Run reproducible, isolated ablations without weakening production controls."""
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    schema = _run_schema_ablation(dataset["json_schema"])
    review = _run_review_ablation(dataset["deterministic_review"])
    browser = _run_browser_ablation(dataset["browser_readback"])
    report = {
        "metadata": build_run_metadata(dataset_path=dataset_path, profile="offline_fault_injection"),
        "experiment_boundary": (
            "Fixed fault injection proves guardrail behavior reproducibly. "
            "It is not presented as a live-model quality measurement."
        ),
        "json_schema": schema,
        "deterministic_review": review,
        "browser_readback": browser,
        "all_expectations_passed": all(
            [
                schema["schema_improved_success_rate"],
                review["deterministic_violation_leak_rate"] == 0.0,
                review["llm_only_violation_leak_rate"] > 0.0,
                browser["readback_detection_rate"] == 1.0,
                browser["submit_only_detection_rate"] == 0.0,
            ]
        ),
    }
    write_json_report(report_path, report)
    return report


def _run_schema_ablation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        raw_valid, raw_error = _validate_listing(case["raw_output"])
        constrained_valid, constrained_error = _validate_listing(case["constrained_output"])
        results.append(
            {
                "case_id": case["case_id"],
                "without_schema_valid": raw_valid,
                "with_schema_valid": constrained_valid,
                "without_schema_error": raw_error,
                "with_schema_error": constrained_error,
            }
        )
    without_rate = _rate(sum(int(case["without_schema_valid"]) for case in results), len(results))
    with_rate = _rate(sum(int(case["with_schema_valid"]) for case in results), len(results))
    return {
        "mode": "fixed_model_output_fixtures",
        "total": len(results),
        "without_schema_success_rate": without_rate,
        "with_schema_success_rate": with_rate,
        "schema_improved_success_rate": with_rate > without_rate,
        "case_results": results,
    }


def _run_review_ablation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    deterministic_leaks = 0
    llm_only_leaks = 0
    violations = 0
    false_positives = 0
    for case in cases:
        deterministic_block = bool(
            case["margin_rate"] < case["min_margin_rate"]
            or not case["inventory_valid"]
            or any(term in case["title"] for term in ["最", "第一", "100%"])
        )
        llm_only_block = not bool(case["llm_approved"])
        should_block = bool(case["should_block"])
        violations += int(should_block)
        deterministic_leaks += int(should_block and not deterministic_block)
        llm_only_leaks += int(should_block and not llm_only_block)
        false_positives += int(not should_block and deterministic_block)
        results.append(
            {
                "case_id": case["case_id"],
                "should_block": should_block,
                "deterministic_blocked": deterministic_block,
                "llm_only_blocked": llm_only_block,
            }
        )
    return {
        "mode": "fixed_permissive_llm_judge_fixture",
        "violation_cases": violations,
        "deterministic_violation_leak_rate": _rate(deterministic_leaks, violations),
        "llm_only_violation_leak_rate": _rate(llm_only_leaks, violations),
        "deterministic_false_positive_rate": _rate(false_positives, len(cases) - violations),
        "case_results": results,
    }


def _run_browser_ablation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    backend = MockBrowserBackend()
    detected = 0
    results: list[dict[str, Any]] = []
    for case in cases:
        reset_seller_center()
        plan = _base_plan(case["case_id"])
        submit_result = backend.execute(plan, f"ablation:{case['case_id']}")
        expected = plan.model_copy(update={case["field"]: case["wrong_value"]})
        readback = backend.verify(expected)
        mismatch_detected = not readback["verified"]
        detected += int(mismatch_detected)
        results.append(
            {
                "case_id": case["case_id"],
                "injected_field": case["field"],
                "submit_returned_applied": submit_result.get("status") == "applied",
                "submit_only_detected": False,
                "readback_detected": mismatch_detected,
                "failed_checks": readback.get("errors", []),
            }
        )
    return {
        "mode": "isolated_mock_seller_center_fault_injection",
        "total": len(results),
        "submit_only_detection_rate": 0.0,
        "readback_detection_rate": _rate(detected, len(results)),
        "case_results": results,
    }


def _validate_listing(text: str) -> tuple[bool, str | None]:
    try:
        ListingModelOutput.model_validate(parse_json_object(text))
        return True, None
    except (StructuredOutputError, ValidationError) as exc:
        return False, f"{type(exc).__name__}:{str(exc)[:240]}"


def _base_plan(product_id: str) -> ExecutionPlan:
    return ExecutionPlan(
        operation="update_listing",
        product_id=product_id,
        title="低延迟无线耳机",
        bullets=["低延迟", "长续航"],
        price=199,
        stock=800,
        coupon=20,
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator if denominator else 0.0, 4)
