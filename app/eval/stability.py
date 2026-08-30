from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


NORMAL_PROFILES = frozenset({"normal", "normal_listing"})
SUCCESS_STATUSES = frozenset({"completed"})
TRUNCATION_MARKERS = (
    "truncat",
    "incomplete",
    "max_output",
    "length_limit",
    "output_too_long",
)
TOOL_OVERFLOW_MARKERS = (
    "too many evidence",
    "evidence tool limit",
    "tool limit",
    "超过上限",
)
SELECTION_MARKERS = (
    "candidate selection",
    "selected_candidate",
    "selection output",
    "候选选择",
)


def percentile(values: Iterable[float | int], percentile_rank: float) -> float | None:
    """Return a linearly interpolated percentile without a statistics dependency."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = min(100.0, max(0.0, float(percentile_rank))) / 100.0
    position = rank * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize_stability_runs(runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [dict(run) for run in runs]
    normal_runs = [
        run
        for run in normalized
        if str(run.get("profile") or run.get("scenario")) in NORMAL_PROFILES
    ]
    strategy_calls = [_strategy_model_calls(run) for run in normalized]
    reductions = [
        reduction
        for run in normalized
        if (reduction := _strategy_context_reduction(run)) is not None
    ]
    overflow_failures = [run for run in normalized if _failed_with(run, TOOL_OVERFLOW_MARKERS)]
    eligible_selection_failures = [
        run for run in normalized if _eligible_selection_failure(run)
    ]
    truncation_runs = [run for run in normalized if _contains(run, TRUNCATION_MARKERS)]
    recovered_truncations = [
        run for run in truncation_runs if str(run.get("status")) in SUCCESS_STATUSES
    ]
    degradations = [
        degradation
        for run in normalized
        for degradation in (run.get("degradations") or [])
        if isinstance(degradation, Mapping)
    ]
    traced_degradations = [item for item in degradations if _degradation_is_traceable(item)]
    real_records = [
        record
        for run in normalized
        for record in (run.get("model_records") or [])
        if _is_real_model_record(record)
    ]
    all_records = [
        record
        for run in normalized
        for record in (run.get("model_records") or [])
        if isinstance(record, Mapping)
    ]

    metrics = {
        "run_count": len(normalized),
        "normal_run_count": len(normal_runs),
        "normal_success_rate": _ratio(
            sum(str(run.get("status")) in SUCCESS_STATUSES for run in normal_runs),
            len(normal_runs),
        ),
        "strategy_model_calls_p95": percentile(strategy_calls, 95),
        "strategy_model_calls_max": max(strategy_calls, default=0),
        "strategy_context_reduction_p95": percentile(reductions, 95),
        "tool_overflow_failure_count": len(overflow_failures),
        "eligible_selection_failure_count": len(eligible_selection_failures),
        "truncation_case_count": len(truncation_runs),
        "truncation_recovery_rate": _ratio(
            len(recovered_truncations), len(truncation_runs)
        ),
        "degradation_count": len(degradations),
        "traceable_degradation_rate": _ratio(
            len(traced_degradations), len(degradations)
        ),
        "model_record_count": len(all_records),
        "real_model_record_count": len(real_records),
        "all_model_records_are_real": bool(all_records)
        and len(real_records) == len(all_records),
    }
    gates = {
        "normal_e2e_success_at_least_95_percent": _gate_ratio(
            metrics["normal_success_rate"], minimum=0.95
        ),
        "strategy_model_calls_p95_at_most_4": _gate_maximum(
            metrics["strategy_model_calls_p95"], maximum=4.0
        ),
        "strategy_context_reduction_at_least_35_percent": _gate_ratio(
            metrics["strategy_context_reduction_p95"], minimum=0.35
        ),
        "tool_overflow_task_failures_zero": _gate_zero(
            metrics["tool_overflow_failure_count"]
        ),
        "eligible_candidate_selection_failures_zero": _gate_zero(
            metrics["eligible_selection_failure_count"]
        ),
        "truncation_recovery_100_percent": (
            _gate_ratio(metrics["truncation_recovery_rate"], minimum=1.0)
            if truncation_runs
            else _not_observed("no truncation fault was exercised")
        ),
        "all_degradations_traceable": (
            _gate_ratio(metrics["traceable_degradation_rate"], minimum=1.0)
            if degradations
            else _not_observed("no degradation was exercised")
        ),
        "real_deepseek_records_observed": {
            "status": "pass" if real_records else "not_observed",
            "observed": len(real_records),
            "expected": ">=1",
        },
    }
    failed = [name for name, gate in gates.items() if gate["status"] == "fail"]
    missing = [
        name for name, gate in gates.items() if gate["status"] == "not_observed"
    ]
    return {
        "protocol_version": "v64-stability-1.0",
        "status": "failed" if failed else ("incomplete" if missing else "passed"),
        "metrics": metrics,
        "gates": gates,
        "failed_gates": failed,
        "not_observed_gates": missing,
    }


def _strategy_model_calls(run: Mapping[str, Any]) -> int:
    records = run.get("model_records") or []
    return sum(
        1
        for record in records
        if isinstance(record, Mapping)
        and record.get("agent_name") == "strategy_agent"
    )


def _strategy_context_reduction(run: Mapping[str, Any]) -> float | None:
    usage = run.get("context_usage") or {}
    stage = usage.get("strategy_agent:stage", {}) if isinstance(usage, Mapping) else {}
    source = float(stage.get("source_context_tokens") or 0)
    selected = float(stage.get("stage_context_tokens") or 0)
    if source <= 0:
        return None
    return max(0.0, min(1.0, 1.0 - selected / source))


def _eligible_selection_failure(run: Mapping[str, Any]) -> bool:
    if str(run.get("status")) in SUCCESS_STATUSES:
        return False
    strategy = (run.get("agent_outputs") or {}).get("strategy_agent", {})
    eligible = any(
        bool(item.get("eligible"))
        for item in strategy.get("candidate_evaluations", [])
        if isinstance(item, Mapping)
    )
    return eligible and _contains(run, SELECTION_MARKERS)


def _failed_with(run: Mapping[str, Any], markers: tuple[str, ...]) -> bool:
    return str(run.get("status")) not in SUCCESS_STATUSES and _contains(run, markers)


def _contains(value: Any, markers: tuple[str, ...]) -> bool:
    text = str(value).lower()
    return any(marker in text for marker in markers)


def _degradation_is_traceable(item: Mapping[str, Any]) -> bool:
    return bool(
        item.get("code")
        and (item.get("stage") or item.get("agent_name"))
        and (item.get("trace_refs") or item.get("developer_message"))
    )


def _is_real_model_record(record: Any) -> bool:
    return isinstance(record, Mapping) and record.get("provider") not in {
        None,
        "deterministic",
    } and record.get("usage_source") == "actual"


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _gate_ratio(value: float | None, *, minimum: float) -> dict[str, Any]:
    if value is None:
        return _not_observed("no applicable samples")
    return {
        "status": "pass" if value >= minimum else "fail",
        "observed": round(value, 4),
        "expected": f">={minimum}",
    }


def _gate_maximum(value: float | None, *, maximum: float) -> dict[str, Any]:
    if value is None:
        return _not_observed("no applicable samples")
    return {
        "status": "pass" if value <= maximum else "fail",
        "observed": round(value, 4),
        "expected": f"<={maximum}",
    }


def _gate_zero(value: int) -> dict[str, Any]:
    return {
        "status": "pass" if value == 0 else "fail",
        "observed": value,
        "expected": "0",
    }


def _not_observed(reason: str) -> dict[str, Any]:
    return {"status": "not_observed", "reason": reason}
