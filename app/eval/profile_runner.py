from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.eval.llm_eval import _percentile, _rate
from app.eval.metadata import build_run_metadata, write_json_report
from app.model.runtime import get_llm_runtime_status
from app.orchestration.workflow import run_workflow


def run_profile_eval(
    dataset_path: Path,
    report_path: Path,
    *,
    profile: str,
    require_live: bool = False,
) -> dict[str, Any]:
    runtime = get_llm_runtime_status()
    if require_live and (not runtime["real_llm_enabled"] or not runtime["ready"]):
        raise RuntimeError(f"Live LLM runtime is not ready: {runtime['issues']}")
    tasks = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    model_records: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    for item in tasks:
        state = run_workflow(item["goal"], approved=False)
        completed_records = [
            record for record in state.model_records if record.get("status") == "completed"
        ]
        model_records.extend(state.model_records)
        fallbacks.extend(state.model_fallbacks)
        violations = state.agent_outputs.get("review_agent", {}).get("violations", [])
        cases.append(
            {
                "case_id": item["case_id"],
                "task_id": state.task_id,
                "run_id": state.run_id,
                "status": state.status,
                "case_success": state.status == "waiting_for_approval" and not violations,
                "hard_constraints_satisfied": not violations,
                "model_call_count": len(state.model_records),
                "structured_calls_passed": all(
                    record.get("structured_validation") == "passed"
                    for record in completed_records
                )
                if completed_records
                else None,
                "fallback_count": len(state.model_fallbacks),
                "listing": state.agent_outputs.get("listing_agent", {}),
            }
        )
    completed = [record for record in model_records if record.get("status") == "completed"]
    structured = [
        record for record in completed if record.get("structured_validation") is not None
    ]
    durations = [float(record.get("duration_ms", 0.0)) for record in completed]
    total_cost = sum(float(record.get("cost_usd_estimate", 0.0)) for record in completed)
    report = {
        "metadata": build_run_metadata(
            dataset_path=dataset_path, profile=profile, runtime=runtime
        ),
        "total_cases": len(cases),
        "task_success_rate": _rate(sum(int(case["case_success"]) for case in cases), len(cases)),
        "hard_constraint_satisfaction_rate": _rate(
            sum(int(case["hard_constraints_satisfied"]) for case in cases), len(cases)
        ),
        "model_call_count": len(model_records),
        "model_call_success_rate": _rate(len(completed), len(model_records)),
        "structured_output_success_rate": _rate(
            sum(int(record.get("structured_validation") == "passed") for record in structured),
            len(structured),
        )
        if structured
        else None,
        "json_repair_count": sum(int(bool(record.get("repaired"))) for record in completed),
        "structured_output_modes": dict(
            Counter(str(record.get("structured_output_mode", "unknown")) for record in completed)
        ),
        "fallback_count": len(fallbacks),
        "fallback_rate": _rate(len(fallbacks), len(cases) * 3),
        "actual_usage_rate": _rate(
            sum(int(record.get("usage_source") == "actual") for record in completed),
            len(completed),
        ),
        "input_tokens": sum(int(record.get("input_tokens", 0)) for record in completed),
        "output_tokens": sum(int(record.get("output_tokens", 0)) for record in completed),
        "total_tokens": sum(int(record.get("total_tokens", 0)) for record in completed),
        "cost_usd_estimate": round(total_cost, 8),
        "avg_cost_per_case_usd": round(total_cost / len(cases) if cases else 0.0, 8),
        "latency_ms": {
            "average": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
        },
        "cases": cases,
    }
    write_json_report(report_path, report)
    return report
