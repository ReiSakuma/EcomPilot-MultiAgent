from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.eval.metadata import build_run_metadata, write_json_report


def build_final_report(report_root: Path) -> dict[str, Any]:
    raw = report_root / "raw"
    suite = _read(raw / "suite_status.json")
    real_browser_requested = bool((suite or {}).get("real_browser_requested"))
    browser_path = raw / (
        "browser_playwright.json" if real_browser_requested else "browser_mock.json"
    )
    comparison = _read(raw / "llm_comparison.json")
    live = (
        _read(raw / "profile_live_llm.json")
        if comparison and comparison.get("status") == "completed"
        else None
    )
    sources = {
        "interview": _read(raw / "interview_offline.json"),
        "business": _read(raw / "business_regression.json"),
        "tool": _read(raw / "tool_reliability.json"),
        "recovery": _read(raw / "recovery_eval.json"),
        "browser": _read(browser_path),
        "ablation": _read(raw / "ablation_offline.json"),
        "baseline": _read(raw / "profile_deterministic.json"),
        "live": live,
        "comparison": comparison,
        "suite": suite,
    }
    interview = sources["interview"] or {}
    baseline = sources["baseline"] or {}
    live = sources["live"]
    browser = sources["browser"] or {}
    report = {
        "metadata": build_run_metadata(profile="final_interview_report"),
        "evidence_status": {
            "offline_suite": "completed" if interview else "not_run",
            "real_llm": "completed" if live else "not_run",
            "real_browser": (
                "completed"
                if browser.get("runtime", {}).get("real_browser_enabled")
                and browser.get("pass_rate") == 1.0
                else "not_run"
            ),
        },
        "comparison_table": [
            _profile_row("Deterministic Baseline", baseline, interview, browser),
            _profile_row("Real LLM", live, interview, browser) if live else _not_run_row("Real LLM"),
            {
                **_profile_row("Full Interview Edition", live or baseline, interview, browser),
                "status": "completed" if live else "browser_completed_live_llm_not_run",
            },
        ],
        "acceptance": {
            "unit_tests_passed": _suite_stage_passed(sources["suite"], "unit_tests"),
            "interview_regression_pass_rate": interview.get("regression_pass_rate"),
            "hard_constraint_satisfaction_rate": interview.get(
                "hard_constraint_satisfaction_rate"
            ),
            "unauthorized_side_effect_count": interview.get(
                "unauthorized_side_effect_count"
            ),
            "tool_eval_pass_rate": (sources["tool"] or {}).get("pass_rate"),
            "recovery_eval_pass_rate": (sources["recovery"] or {}).get(
                "recovery_pass_rate"
            ),
            "browser_eval_pass_rate": browser.get("pass_rate"),
            "ablation_expectations_passed": (sources["ablation"] or {}).get(
                "all_expectations_passed"
            ),
        },
        "failed_case_count": len(
            [case for case in interview.get("case_results", []) if not case.get("passed")]
        ),
        "source_reports": {
            key: f"reports/raw/{filename}"
            for key, filename in {
                "interview": "interview_offline.json",
                "business": "business_regression.json",
                "tool": "tool_reliability.json",
                "recovery": "recovery_eval.json",
                "browser": browser_path.name,
                "ablation": "ablation_offline.json",
                "comparison": "llm_comparison.json",
            }.items()
            if sources[key] is not None
        },
    }
    write_json_report(report_root / "final_report.json", report)
    (report_root / "summaries").mkdir(parents=True, exist_ok=True)
    (report_root / "summaries" / "FINAL_REPORT.md").write_text(
        _markdown(report), encoding="utf-8"
    )
    return report


def _profile_row(
    name: str,
    profile: dict[str, Any] | None,
    interview: dict[str, Any],
    browser: dict[str, Any],
) -> dict[str, Any]:
    profile = profile or {}
    return {
        "profile": name,
        "status": "completed",
        "task_success": profile.get("task_success_rate", interview.get("regression_pass_rate")),
        "constraint": profile.get(
            "hard_constraint_satisfaction_rate",
            interview.get("hard_constraint_satisfaction_rate"),
        ),
        "structured": profile.get("structured_output_success_rate"),
        "browser_verify": browser.get("pass_rate"),
        "fallback": profile.get("fallback_rate", 0.0),
        "p95_latency_ms": (profile.get("latency_ms") or {}).get("p95"),
        "cost_usd": profile.get("cost_usd_estimate", 0.0),
    }


def _not_run_row(name: str) -> dict[str, Any]:
    return {"profile": name, "status": "not_run"}


def _suite_stage_passed(suite: dict[str, Any] | None, name: str) -> bool | None:
    if not suite:
        return None
    for stage in suite.get("stages", []):
        if stage.get("name") == name:
            return stage.get("status") == "passed"
    return None


def _read(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# EcomPilot Interview Edition Final Report",
        "",
        f"Generated: {report['metadata']['executed_at']}",
        "",
        "## Evidence Status",
        "",
    ]
    for key, value in report["evidence_status"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Comparison",
            "",
            "| Profile | Status | Task | Constraint | Structured | Browser Eval | Fallback | P95 ms | Cost USD |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["comparison_table"]:
        lines.append(
            "| {profile} | {status} | {task_success} | {constraint} | {structured} | "
            "{browser_verify} | {fallback} | {p95_latency_ms} | {cost_usd} |".format(
                **{
                    "task_success": "N/A",
                    "constraint": "N/A",
                    "structured": "N/A",
                    "browser_verify": "N/A",
                    "fallback": "N/A",
                    "p95_latency_ms": "N/A",
                    "cost_usd": "N/A",
                    **{key: ("N/A" if value is None else value) for key, value in row.items()},
                }
            )
        )
    lines.extend(["", "## Acceptance", ""])
    for key, value in report["acceptance"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "> `not_run` means no valid external evidence was produced. It is not a failure and is never replaced with mock data.",
            "",
        ]
    )
    return "\n".join(lines)
