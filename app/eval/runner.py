from __future__ import annotations

import json
from pathlib import Path

from app.config import DATA_DIR
from app.eval.badcase import classify_bad_case
from app.eval.metrics import (
    average_context_tokens,
    constraint_satisfaction,
    side_effect_tool_count,
    task_success,
    tool_failure_rate,
    model_call_count,
    market_sample_score,
    execution_verification_rate,
    idempotent_replay_count,
    tool_retry_count,
    tool_validation_failure_count,
)
from app.orchestration.workflow import run_workflow
from app.observability.store import TraceStore


def run_eval(dataset_path: Path, report_path: Path | None = None) -> dict[str, object]:
    tasks = json.loads(dataset_path.read_text(encoding="utf-8"))
    total = len(tasks)
    case_results = []
    states = []
    trace_summaries = []
    for item in tasks:
        state = run_workflow(item["goal"], approved=item.get("approved", False))
        states.append(state)
        trace_summaries.append(TraceStore().get_summary(state.run_id))
        success = task_success(state)
        bad_case = None if success else classify_bad_case(state)
        expected_success = item.get("expected_success")
        expected_bad_case = item.get("expected_bad_case")
        passed_expectation = True
        if expected_success is not None:
            passed_expectation = passed_expectation and success is bool(expected_success)
        if expected_bad_case is not None:
            passed_expectation = (
                passed_expectation
                and bad_case is not None
                and bad_case.get("case_type") == expected_bad_case
            )
        case_results.append(
            {
                "case_id": item.get("case_id", state.task_id),
                "name": item.get("name", ""),
                "task_id": state.task_id,
                "run_id": state.run_id,
                "status": state.status,
                "success": success,
                "expected_success": expected_success,
                "expected_bad_case": expected_bad_case,
                "bad_case": bad_case,
                "passed_expectation": passed_expectation,
            }
        )

    successes = sum(int(task_success(state)) for state in states)
    constraints = sum(int(constraint_satisfaction(state)) for state in states)
    report: dict[str, object] = {
        "total": total,
        "task_success_rate": successes / total if total else 0.0,
        "constraint_satisfaction_rate": constraints / total if total else 0.0,
        "avg_context_tokens": round(
            sum(average_context_tokens(state) for state in states) / total if total else 0.0, 2
        ),
        "avg_tool_failure_rate": round(
            sum(tool_failure_rate(state) for state in states) / total if total else 0.0, 4
        ),
        "side_effect_tool_calls": sum(side_effect_tool_count(state) for state in states),
        "tool_retry_count": sum(tool_retry_count(state) for state in states),
        "idempotent_replay_count": sum(idempotent_replay_count(state) for state in states),
        "tool_validation_failure_count": sum(
            tool_validation_failure_count(state) for state in states
        ),
        "model_call_count": sum(model_call_count(state) for state in states),
        "avg_market_sample_score": round(
            sum(market_sample_score(state) for state in states) / total if total else 0.0,
            4,
        ),
        "execution_verification_rate": round(
            sum(execution_verification_rate(state) for state in states) / total if total else 0.0,
            4,
        ),
        "regression_pass_rate": round(
            sum(int(case["passed_expectation"]) for case in case_results) / total if total else 0.0,
            4,
        ),
        "bad_case_counts": _bad_case_counts(case_results),
        "bad_cases": [classify_bad_case(state) for state in states if not task_success(state)],
        "case_results": case_results,
        "regression_failures": [
            case for case in case_results if not case["passed_expectation"]
        ],
        "observability": {
            "traced_run_count": len(trace_summaries),
            "trace_coverage_rate": round(len(trace_summaries) / total if total else 0.0, 4),
            "avg_events_per_run": round(
                sum(summary["event_count"] for summary in trace_summaries) / total
                if total
                else 0.0,
                2,
            ),
            "tool_call_events": sum(summary["tool_call_count"] for summary in trace_summaries),
            "model_call_events": sum(summary["model_call_count"] for summary in trace_summaries),
            "error_events": sum(summary["error_count"] for summary in trace_summaries),
            "failed_status_events": sum(
                summary["failed_status_event_count"] for summary in trace_summaries
            ),
            "guardrail_events": sum(
                summary["guardrail_event_count"] for summary in trace_summaries
            ),
        },
    }
    if report_path is None:
        report_path = DATA_DIR / "eval" / "v4_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _bad_case_counts(case_results: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in case_results:
        bad_case = case.get("bad_case")
        if not bad_case:
            continue
        case_type = str(bad_case.get("case_type"))
        counts[case_type] = counts.get(case_type, 0) + 1
    return counts
