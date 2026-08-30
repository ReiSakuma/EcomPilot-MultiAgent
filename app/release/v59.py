from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.browser.runtime import get_browser_runtime_status
from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.model.runtime import get_llm_runtime_status
from app.orchestration.state import TaskState


class V59EvidenceStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: Literal["passed", "failed", "not_run", "external_blocked"]
    evidence_path: str
    detail: str


class V59ReleaseStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release: Literal["v59-final"] = "v59-final"
    project_version: str
    generated_at: datetime
    interview_ready: bool
    real_external_chain_validated: bool
    production_ready: bool = False
    stages: tuple[V59EvidenceStage, ...]
    runtime: dict[str, Any] = Field(default_factory=dict)
    boundaries: tuple[str, ...]


REPORTS = {
    "offline": PROJECT_ROOT / "reports" / "v59" / "offline_acceptance.json",
    "compatibility": PROJECT_ROOT / "reports" / "v59" / "compatibility.json",
    "run_bundles": PROJECT_ROOT / "reports" / "v59" / "run_bundle_acceptance.json",
    "browser": PROJECT_ROOT / "reports" / "v59" / "browser_acceptance.json",
    "live_deepseek": PROJECT_ROOT / "reports" / "v59" / "live_deepseek_suite.json",
}


def build_route_evidence(state: TaskState) -> dict[str, Any]:
    outputs = state.agent_outputs
    market = outputs.get("market_agent") or {}
    price_gate = outputs.get("market_price_gate_agent") or {}
    strategy = outputs.get("strategy_agent") or {}
    listing = outputs.get("listing_agent") or {}
    review = outputs.get("review_agent") or {}
    browser = outputs.get("browser_agent") or {}
    evaluations = list(strategy.get("candidate_evaluations") or [])
    corrections = [
        *list(listing.get("semantic_corrections") or []),
        *list(strategy.get("semantic_corrections") or []),
        *list(review.get("correction_audit") or []),
    ]
    stages = {
        "market_data_cleaning": _observed(
            market.get("market_statistics"),
            detail={
                "reference_method": market.get("reference_method"),
                "statistics": market.get("market_statistics"),
            },
        ),
        "three_layer_classification": _observed(
            market.get("market_layers"), detail=market.get("market_layers")
        ),
        "market_price_gate": _observed(
            price_gate or market.get("price_assessment"),
            detail=price_gate or market.get("price_assessment"),
        ),
        "candidate_generation": _observed(
            strategy.get("candidate_proposals"),
            detail={
                "protocol_version": strategy.get("candidate_protocol_version"),
                "candidate_count": len(strategy.get("candidate_proposals") or []),
            },
        ),
        "tool_adjudication": _observed(
            evaluations,
            detail={
                "evaluated": len(evaluations),
                "eligible": sum(bool(item.get("eligible")) for item in evaluations),
            },
        ),
        "model_selection": _observed(
            strategy.get("selected_candidate_id"),
            detail={"selected_candidate_id": strategy.get("selected_candidate_id")},
        ),
        "deterministic_render": _observed(
            strategy.get("render_manifest"),
            detail={
                "strategy_render_version": strategy.get("strategy_render_version"),
                "numeric_ownership": strategy.get("numeric_ownership"),
                "render_manifest": strategy.get("render_manifest"),
            },
        ),
        "correction_audit": {
            "status": "observed" if corrections else "not_triggered",
            "count": len(corrections),
            "items": corrections,
        },
        "browser_evidence": _observed(
            browser,
            detail={
                "browser_result": browser.get("browser_result"),
                "verification": browser.get("verification"),
            },
        ),
    }
    return {
        "protocol_version": "1.0",
        "task_id": state.task_id,
        "run_id": state.run_id,
        "checkpoint_version": state.checkpoint_version,
        "outcome": state.outcome.value,
        "stages": stages,
    }


def build_linkage_identity(
    state: TaskState,
    *,
    trace_chain: list[dict[str, Any]] | None = None,
    seller_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution_plan = (
        (state.agent_outputs.get("review_agent") or {}).get("execution_plan") or {}
    )
    execution_checkpoint_version = execution_plan.get(
        "checkpoint_version", state.checkpoint_version
    )
    expected = {
        "task_id": state.task_id,
        "run_id": state.run_id,
        "checkpoint_version": state.checkpoint_version,
        "execution_checkpoint_version": execution_checkpoint_version,
    }
    trace_summary = ((trace_chain or [{}])[0].get("summary") or {}) if trace_chain else {}
    seller = (seller_snapshot or {}).get("last_execution") or {}
    surfaces = {
        "user": expected,
        "ops": expected,
        "trace": {
            "task_id": trace_summary.get("task_id"),
            "run_id": trace_summary.get("run_id"),
            "checkpoint_version": expected["checkpoint_version"],
            "execution_checkpoint_version": execution_checkpoint_version,
        },
        "seller_center": {
            "task_id": seller.get("task_id"),
            "run_id": seller.get("run_id"),
            "checkpoint_version": seller.get("checkpoint_version"),
            "execution_checkpoint_version": seller.get("checkpoint_version"),
        },
    }
    applicable = {
        name: value
        for name, value in surfaces.items()
        if name in {"user", "ops"} or any(item is not None for item in value.values())
    }
    mismatches = {
        name: value
        for name, value in applicable.items()
        if (
            value.get("task_id") != expected["task_id"]
            or value.get("run_id") != expected["run_id"]
            or (
                value.get("checkpoint_version")
                != (
                    expected["execution_checkpoint_version"]
                    if name == "seller_center"
                    else expected["checkpoint_version"]
                )
            )
        )
    }
    return {
        "protocol_version": "1.0",
        "expected": expected,
        "surfaces": surfaces,
        "consistent": not mismatches,
        "mismatches": mismatches,
    }


def validate_live_deepseek_report(payload: dict[str, Any]) -> dict[str, Any]:
    runs = payload.get("runs")
    if not isinstance(runs, list):
        runs = [payload]
    issues: list[str] = []
    observed_calls = 0
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            issues.append(f"run_{index}:invalid_record")
            continue
        records = run.get("model_records") or []
        if not records:
            issues.append(f"run_{index}:no_model_calls")
        if run.get("model_fallbacks"):
            issues.append(f"run_{index}:model_fallback_observed")
        for record in records:
            if record.get("provider") != "deepseek":
                issues.append(f"run_{index}:provider_not_deepseek")
            if record.get("status") != "completed":
                issues.append(f"run_{index}:model_call_not_completed")
            if record.get("usage_source") != "actual":
                issues.append(f"run_{index}:usage_not_actual")
            observed_calls += int(record.get("status") == "completed")
    return {
        "valid": bool(runs) and observed_calls > 0 and not issues,
        "run_count": len(runs),
        "model_call_count": observed_calls,
        "issues": sorted(set(issues)),
    }


def build_v59_release_status() -> V59ReleaseStatus:
    stages = (
        _report_stage("offline"),
        _report_stage("compatibility"),
        _report_stage("run_bundles"),
        _report_stage("browser"),
        _report_stage("live_deepseek", live=True),
    )
    return V59ReleaseStatus(
        project_version=PROJECT_VERSION,
        generated_at=datetime.now(timezone.utc),
        interview_ready=all(item.status == "passed" for item in stages[:4]),
        real_external_chain_validated=stages[-1].status == "passed",
        stages=stages,
        runtime={
            "llm": _public_runtime(get_llm_runtime_status()),
            "browser": _public_runtime(get_browser_runtime_status()),
        },
        boundaries=(
            "真实 DeepSeek 只有 provider、实际 Token 和无降级记录同时成立才算通过。",
            "模拟 Seller Center 不代表任何真实电商平台生产账户。",
            "SQLite 和单机内存熔断器是面试参考实现，不声明跨主机生产高可用。",
            "外部网络不可用会标记 external_blocked，不用 Mock 冒充通过。",
        ),
    )


def evidence_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _observed(value: Any, *, detail: Any) -> dict[str, Any]:
    return {"status": "observed" if value else "not_reached", "detail": detail}


def _report_stage(name: str, *, live: bool = False) -> V59EvidenceStage:
    path = REPORTS[name]
    try:
        relative = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        relative = path.name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return V59EvidenceStage(
            name=name,
            status="not_run",
            evidence_path=relative,
            detail="尚未生成该验收报告。",
        )
    if live:
        if payload.get("status") in {"not_run", "external_blocked"}:
            return V59EvidenceStage(
                name=name,
                status="external_blocked",
                evidence_path=relative,
                detail=str(payload.get("error") or "真实外部链路尚未执行。"),
            )
        validation = validate_live_deepseek_report(payload)
        passed = validation["valid"]
        detail = "真实 DeepSeek 证据有效。" if passed else "; ".join(validation["issues"])
    else:
        passed = payload.get("passed") is True
        detail = str(payload.get("detail") or ("验收通过。" if passed else "报告未通过。"))
    return V59EvidenceStage(
        name=name,
        status="passed" if passed else "failed",
        evidence_path=relative,
        detail=detail,
    )


def _public_runtime(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "ready",
        "provider",
        "model",
        "fallback_mode",
        "real_llm_enabled",
        "backend",
        "issues",
    }
    return {key: item for key, item in value.items() if key in allowed}
