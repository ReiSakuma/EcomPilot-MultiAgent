from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from app.eval.metadata import write_json_report


def build_llm_comparison(
    baseline_path: Path,
    live_path: Path | None,
    output_path: Path,
    blind_sheet_path: Path,
) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    live = json.loads(live_path.read_text(encoding="utf-8")) if live_path and live_path.exists() else None
    report = {
        "status": "completed" if live is not None else "live_not_run",
        "honest_boundary": (
            "Live metrics are absent until an external provider run succeeds. "
            "Deterministic numbers are never copied into the live column."
        ),
        "deterministic_baseline": _summary(baseline),
        "real_llm": _summary(live) if live else None,
        "delta": _delta(baseline, live) if live else None,
        "blind_review_sheet": str(blind_sheet_path) if live else None,
    }
    if live:
        _write_blind_sheet(baseline, live, blind_sheet_path)
    write_json_report(output_path, report)
    return report


def _summary(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    keys = [
        "task_success_rate",
        "hard_constraint_satisfaction_rate",
        "model_call_count",
        "model_call_success_rate",
        "structured_output_success_rate",
        "structured_output_modes",
        "fallback_rate",
        "actual_usage_rate",
        "total_tokens",
        "cost_usd_estimate",
        "avg_cost_per_case_usd",
        "latency_ms",
    ]
    return {key: report.get(key) for key in keys}


def _delta(baseline: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    keys = ["task_success_rate", "hard_constraint_satisfaction_rate", "fallback_rate"]
    return {
        key: round(float(live.get(key, 0.0)) - float(baseline.get(key, 0.0)), 4)
        for key in keys
    }


def _write_blind_sheet(
    baseline: dict[str, Any], live: dict[str, Any], target: Path
) -> None:
    baseline_cases = {case["case_id"]: case for case in baseline["cases"]}
    live_cases = {case["case_id"]: case for case in live["cases"]}
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "candidate_a",
                "candidate_b",
                "a_source_hash",
                "b_source_hash",
                "relevance_1_5",
                "clarity_1_5",
                "compliance_1_5",
                "preferred",
                "reviewer_notes",
            ],
        )
        writer.writeheader()
        for case_id in sorted(set(baseline_cases) & set(live_cases)):
            deterministic_listing = baseline_cases[case_id].get("listing", {})
            live_listing = live_cases[case_id].get("listing", {})
            swap = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 2 == 0
            a, b = (
                (live_listing, deterministic_listing)
                if swap
                else (deterministic_listing, live_listing)
            )
            writer.writerow(
                {
                    "case_id": case_id,
                    "candidate_a": json.dumps(a, ensure_ascii=False),
                    "candidate_b": json.dumps(b, ensure_ascii=False),
                    "a_source_hash": _source_hash("live" if swap else "deterministic", case_id),
                    "b_source_hash": _source_hash("deterministic" if swap else "live", case_id),
                }
            )


def _source_hash(source: str, case_id: str) -> str:
    return hashlib.sha256(f"{source}:{case_id}".encode()).hexdigest()[:12]
