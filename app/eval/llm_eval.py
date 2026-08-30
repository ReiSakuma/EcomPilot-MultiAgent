from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.eval.metadata import build_run_metadata
from app.model.runtime import get_llm_runtime_status
from app.orchestration.workflow import run_workflow


def run_llm_eval(
    dataset_path: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    tasks = json.loads(dataset_path.read_text(encoding="utf-8"))
    runtime = get_llm_runtime_status()
    enabled_agents = set(runtime["enabled_agents"])
    cases: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    all_fallbacks: list[dict[str, Any]] = []

    for item in tasks:
        state = run_workflow(item["goal"], approved=False)
        all_records.extend(state.model_records)
        all_fallbacks.extend(state.model_fallbacks)
        modes = {
            agent_name: state.agent_outputs.get(agent_name, {}).get("generation_mode")
            for agent_name in enabled_agents
        }
        cases.append(
            {
                "case_id": item.get("case_id", state.task_id),
                "task_id": state.task_id,
                "run_id": state.run_id,
                "workflow_status": state.status,
                "generation_modes": modes,
                "structured_output_passed": bool(modes)
                and all(mode == "llm" for mode in modes.values()),
                "model_call_count": len(state.model_records),
                "fallback_count": len(state.model_fallbacks),
            }
        )

    completed = [record for record in all_records if record.get("status") == "completed"]
    failed = [record for record in all_records if record.get("status") == "failed"]
    structured = [
        record for record in completed if record.get("structured_validation") is not None
    ]
    structured_passed = [
        record for record in structured if record.get("structured_validation") == "passed"
    ]
    total_cases = len(cases)
    durations = [float(record.get("duration_ms", 0.0)) for record in completed]
    total_tokens = sum(int(record.get("total_tokens", 0)) for record in completed)
    report: dict[str, Any] = {
        "metadata": build_run_metadata(
            dataset_path=dataset_path,
            profile="live_llm" if runtime["real_llm_enabled"] else "deterministic_llm_control",
            runtime=runtime,
        ),
        "runtime": runtime,
        "total_cases": total_cases,
        "model_call_count": len(all_records),
        "completed_model_calls": len(completed),
        "failed_model_calls": len(failed),
        "model_call_success_rate": _rate(len(completed), len(all_records)),
        "structured_output_success_rate": _rate(len(structured_passed), len(structured)),
        "case_structured_success_rate": _rate(
            sum(int(case["structured_output_passed"]) for case in cases), total_cases
        ),
        "fallback_count": len(all_fallbacks),
        "fallback_rate": _rate(len(all_fallbacks), total_cases * max(1, len(enabled_agents))),
        "json_repair_count": sum(int(bool(record.get("repaired"))) for record in completed),
        "structured_output_modes": dict(
            Counter(str(record.get("structured_output_mode", "unknown")) for record in completed)
        ),
        "actual_usage_record_count": sum(
            int(record.get("usage_source") == "actual") for record in completed
        ),
        "actual_usage_rate": _rate(
            sum(int(record.get("usage_source") == "actual") for record in completed),
            len(completed),
        ),
        "input_tokens": sum(int(record.get("input_tokens", 0)) for record in completed),
        "output_tokens": sum(int(record.get("output_tokens", 0)) for record in completed),
        "total_tokens": total_tokens,
        "cost_usd_estimate": round(
            sum(float(record.get("cost_usd_estimate", 0.0)) for record in completed), 8
        ),
        "avg_cost_per_case_usd": round(
            sum(float(record.get("cost_usd_estimate", 0.0)) for record in completed)
            / total_cases
            if total_cases
            else 0.0,
            8,
        ),
        "latency_ms": {
            "average": round(statistics.fmean(durations), 2) if durations else 0.0,
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
        },
        "cases": cases,
    }
    target = report_path or DATA_DIR / "eval" / "v14_llm_report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator if denominator else 0.0, 4)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return round(ordered[index], 2)
