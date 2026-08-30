from __future__ import annotations

from pathlib import Path

import pytest

from app.distributed.runtime import DistributedRuntime
from app.operations.assessment import (
    build_capacity_report,
    build_isolation_audit,
    build_operational_readiness,
    build_slo_report,
)
from app.operations.chaos import run_chaos_experiments
from app.operations.models import CapacityReport, IsolationAudit
from app.operations.terminal import project_terminal_outcome
from app.orchestration.failures import TaskOutcome
from app.release.protocols import build_protocol_manifest


def test_v39_chaos_matrix_covers_all_planned_faults() -> None:
    report = run_chaos_experiments()
    assert report.passed is True
    assert {item.fault for item in report.scenarios} == {
        "network_partition", "database_failover", "duplicate_delivery",
        "model_rate_limit", "slow_tool", "summary_pollution",
    }
    assert report.recovered_scenarios == report.total_scenarios == 6
    assert {item.terminal_class for item in report.scenarios} >= {
        "success", "degraded_completed", "waiting_user",
    }


@pytest.mark.parametrize(
    ("source", "degradations", "expected"),
    [
        (TaskOutcome.completed, (), "success"),
        (TaskOutcome.completed, ("optional_tool_timeout",), "degraded_completed"),
        (TaskOutcome.awaiting_approval, (), "waiting_user"),
        (TaskOutcome.business_rejected, (), "business_rejected"),
        (TaskOutcome.needs_attention, (), "manual_attention"),
    ],
)
def test_v39_projects_exactly_five_explainable_terminal_classes(
    source: TaskOutcome, degradations: tuple[str, ...], expected: str
) -> None:
    projected = project_terminal_outcome(source, degradation_refs=degradations)
    assert projected.terminal_class == expected
    assert projected.reason


def test_v39_capacity_and_tenant_isolation_gates() -> None:
    capacity = build_capacity_report(jobs=40, workers=4)
    isolation = build_isolation_audit()
    assert capacity.passed is True
    assert capacity.duplicate_jobs == capacity.dead_jobs == 0
    assert isolation.passed is True
    assert isolation.cross_tenant_leaks == 0


def test_v39_slo_generates_page_alert_for_tenant_leak() -> None:
    chaos = run_chaos_experiments()
    capacity = CapacityReport(
        jobs=20, workers=2, enqueue_throughput_per_second=10,
        drain_throughput_per_second=10, enqueue_p50_ms=1, enqueue_p95_ms=2,
        duplicate_jobs=0, dead_jobs=0, recommended_worker_counts={"workflow": 2},
        passed=True, boundary="test",
    )
    isolation = IsolationAudit(
        checks={"cross_tenant_read": False}, cross_tenant_leaks=1, passed=False
    )
    slo = build_slo_report(chaos, capacity, isolation)
    assert slo.passed is False
    assert any(alert.startswith("page:cross_tenant_leaks") for alert in slo.alerts)


def test_v39_runtime_records_operational_metrics(tmp_path: Path) -> None:
    runtime = DistributedRuntime(tmp_path / "runtime.db", tenant_rate_per_minute=100)
    job, _ = runtime.enqueue(
        tenant_id="tenant_demo", pool="workflow", job_type="metric",
        idempotency_key="metric-1", payload={}, enforce_rate_limit=False,
    )
    replay, replayed = runtime.enqueue(
        tenant_id="tenant_demo", pool="workflow", job_type="metric",
        idempotency_key="metric-1", payload={}, enforce_rate_limit=False,
    )
    assert replayed and replay.job_id == job.job_id
    grant = runtime.lease_next(worker_id="metric-worker", pool="workflow")
    runtime.complete(grant, {"ok": True})
    names = {item["metric_name"] for item in runtime.metric_events(tenant_id="tenant_demo")}
    assert {"queue_enqueued", "idempotency_replay", "queue_wait_ms", "job_execution_ms"} <= names


def test_v39_operational_readiness_and_protocols_are_final_reference() -> None:
    readiness = build_operational_readiness(jobs=40, workers=4)
    manifest = build_protocol_manifest()
    assert readiness.status == "reference_validated"
    assert readiness.production_claimed is False
    assert readiness.five_terminal_states_covered is True
    assert manifest.release == "v39-chaos-readiness"
    contracts = {item.name: item.version for item in manifest.contracts}
    assert contracts["run_bundle"] == "2.5"
    assert contracts["checkpoint_compatibility_diagnostic"] == "1.0"
    assert contracts["terminal_outcome"] == "1.0"
    assert contracts["chaos_experiment"] == "1.0"
