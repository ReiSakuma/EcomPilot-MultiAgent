from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any

from app.config import PROJECT_ROOT
from app.distributed.runtime import DistributedRuntime
from app.operations.chaos import run_chaos_experiments
from app.operations.models import (
    CapacityReport,
    ChaosReport,
    IsolationAudit,
    OperationalReadiness,
    SloIndicator,
    SloReport,
)


REPORT_DIR = PROJECT_ROOT / "reports" / "raw"
CHAOS_REPORT_PATH = REPORT_DIR / "v39_chaos_acceptance.json"
CAPACITY_REPORT_PATH = REPORT_DIR / "v39_capacity_acceptance.json"
ISOLATION_REPORT_PATH = REPORT_DIR / "v39_isolation_audit.json"
SLO_REPORT_PATH = REPORT_DIR / "v39_slo_report.json"
READINESS_REPORT_PATH = REPORT_DIR / "v39_operational_readiness.json"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def build_capacity_report(*, jobs: int = 240, workers: int = 12) -> CapacityReport:
    jobs = max(20, jobs)
    workers = max(1, workers)
    with tempfile.TemporaryDirectory(prefix="ecompilot-v39-capacity-") as directory:
        runtime = DistributedRuntime(
            Path(directory) / "runtime.db", global_queue_limit=jobs + 10,
            tenant_queue_limit=jobs + 10, tenant_rate_per_minute=jobs * 10,
        )
        latencies: list[float] = []

        def enqueue(index: int) -> str:
            started = time.perf_counter()
            job, _ = runtime.enqueue(
                tenant_id=f"tenant_{index % 8}", pool="workflow", job_type="capacity",
                idempotency_key=f"capacity:{index}", payload={"index": index},
                enforce_rate_limit=False,
            )
            latencies.append((time.perf_counter() - started) * 1000)
            return job.job_id

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            ids = list(executor.map(enqueue, range(jobs)))
        enqueue_seconds = max(0.000001, time.perf_counter() - started)

        def drain(index: int) -> str:
            result = runtime.run_once(
                worker_id=f"capacity-{index}", pool="workflow",
                handlers={"capacity": lambda payload: {"accepted": payload["index"]}},
            )
            return result.status if result else "missing"

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            statuses = list(executor.map(drain, range(jobs)))
        drain_seconds = max(0.000001, time.perf_counter() - started)
        dead_jobs = statuses.count("dead") + statuses.count("missing")
        report = CapacityReport(
            jobs=jobs,
            workers=workers,
            enqueue_throughput_per_second=round(jobs / enqueue_seconds, 2),
            drain_throughput_per_second=round(jobs / drain_seconds, 2),
            enqueue_p50_ms=round(median(latencies), 3),
            enqueue_p95_ms=round(_percentile(latencies, 0.95), 3),
            duplicate_jobs=jobs - len(set(ids)),
            dead_jobs=dead_jobs,
            recommended_worker_counts={
                "workflow": workers,
                "model": max(2, workers // 3),
                "sql": max(2, workers // 4),
                "browser": max(1, workers // 6),
            },
            passed=(
                len(set(ids)) == jobs
                and dead_jobs == 0
                and jobs / enqueue_seconds >= 5
                and jobs / drain_seconds >= 5
                and _percentile(latencies, 0.95) <= 2_000
            ),
            boundary=(
                "Single-host SQLite reference benchmark. Worker recommendations are a local "
                "capacity baseline and must be recalibrated on production infrastructure."
            ),
        )
    return report


def build_isolation_audit() -> IsolationAudit:
    with tempfile.TemporaryDirectory(prefix="ecompilot-v39-isolation-") as directory:
        runtime = DistributedRuntime(
            Path(directory) / "runtime.db", global_queue_limit=100,
            tenant_queue_limit=100, tenant_rate_per_minute=1000,
        )
        alpha, _ = runtime.enqueue(
            tenant_id="tenant_alpha", pool="workflow", job_type="audit",
            idempotency_key="same-key", payload={"tenant": "alpha"},
            enforce_rate_limit=False,
        )
        beta, _ = runtime.enqueue(
            tenant_id="tenant_beta", pool="workflow", job_type="audit",
            idempotency_key="same-key", payload={"tenant": "beta"},
            enforce_rate_limit=False,
        )
        cross_read_blocked = False
        try:
            runtime.get_job(alpha.job_id, tenant_id="tenant_beta")
        except KeyError:
            cross_read_blocked = True
        permit_alpha = runtime.prepare_execution(
            tenant_id="tenant_alpha", resource_id="product:shared-name",
            operation="update_listing", plan={"price": 100}, owner_id="alpha-worker",
        )
        permit_beta = runtime.prepare_execution(
            tenant_id="tenant_beta", resource_id="product:shared-name",
            operation="update_listing", plan={"price": 200}, owner_id="beta-worker",
        )
        runtime.confirm_execution(permit_alpha, {"price": 100})
        runtime.confirm_execution(permit_beta, {"price": 200})
        alpha_snapshot = runtime.snapshot(tenant_id="tenant_alpha")
        beta_snapshot = runtime.snapshot(tenant_id="tenant_beta")
        checks = {
            "same_idempotency_key_is_tenant_scoped": alpha.job_id != beta.job_id,
            "cross_tenant_job_read_blocked": cross_read_blocked,
            "same_resource_name_is_tenant_scoped": (
                alpha_snapshot["confirmed_business_effects"] == 1
                and beta_snapshot["confirmed_business_effects"] == 1
            ),
            "tenant_snapshots_do_not_aggregate_other_tenant": (
                alpha_snapshot["confirmed_business_effects"]
                == beta_snapshot["confirmed_business_effects"]
                == 1
            ),
        }
    leaks = sum(not value for value in checks.values())
    return IsolationAudit(checks=checks, cross_tenant_leaks=leaks, passed=leaks == 0)


def build_slo_report(
    chaos: ChaosReport, capacity: CapacityReport, isolation: IsolationAudit
) -> SloReport:
    recovery_rate = chaos.recovered_scenarios / max(1, chaos.total_scenarios)
    indicators = (
        SloIndicator(
            name="chaos_recovery_rate", value=recovery_rate,
            objective=">= 0.99", passed=recovery_rate >= 0.99,
            severity="page" if recovery_rate < 0.99 else "none",
        ),
        SloIndicator(
            name="enqueue_p95_ms", value=capacity.enqueue_p95_ms,
            objective="<= 2000 ms", passed=capacity.enqueue_p95_ms <= 2_000,
            severity="ticket" if capacity.enqueue_p95_ms > 2_000 else "none",
        ),
        SloIndicator(
            name="drain_throughput_per_second", value=capacity.drain_throughput_per_second,
            objective=">= 5 jobs/s", passed=capacity.drain_throughput_per_second >= 5,
            severity="ticket" if capacity.drain_throughput_per_second < 5 else "none",
        ),
        SloIndicator(
            name="duplicate_jobs", value=float(capacity.duplicate_jobs),
            objective="= 0", passed=capacity.duplicate_jobs == 0,
            severity="page" if capacity.duplicate_jobs else "none",
        ),
        SloIndicator(
            name="cross_tenant_leaks", value=float(isolation.cross_tenant_leaks),
            objective="= 0", passed=isolation.cross_tenant_leaks == 0,
            severity="page" if isolation.cross_tenant_leaks else "none",
        ),
    )
    alerts = tuple(
        f"{item.severity}:{item.name}:{item.value} violates {item.objective}"
        for item in indicators if not item.passed
    )
    return SloReport(indicators=indicators, alerts=alerts, passed=not alerts)


def build_operational_readiness(
    *, jobs: int = 240, workers: int = 12, persist: bool = False
) -> OperationalReadiness:
    chaos = run_chaos_experiments()
    capacity = build_capacity_report(jobs=jobs, workers=workers)
    isolation = build_isolation_audit()
    slo = build_slo_report(chaos, capacity, isolation)
    report = OperationalReadiness(
        status=(
            "reference_validated"
            if chaos.passed and capacity.passed and isolation.passed and slo.passed
            else "needs_validation"
        ),
        five_terminal_states_covered=True,
        chaos=chaos,
        capacity=capacity,
        isolation=isolation,
        slo=slo,
        production_boundaries=(
            "身份仍是演示令牌，不是生产 IdP/OIDC。",
            "SQLite/WAL 是单机协议参考，不是跨可用区数据库与消息队列集群。",
            "Seller Center 是模拟商家后台，没有真实平台账户与真实订单流。",
            "故障注入位于可控协议边界，不代表真实云基础设施灾备演练。",
        ),
    )
    if persist:
        _write(CHAOS_REPORT_PATH, chaos.model_dump(mode="json"))
        _write(CAPACITY_REPORT_PATH, capacity.model_dump(mode="json"))
        _write(ISOLATION_REPORT_PATH, isolation.model_dump(mode="json"))
        _write(SLO_REPORT_PATH, slo.model_dump(mode="json"))
        _write(READINESS_REPORT_PATH, report.model_dump(mode="json"))
    return report


def load_operational_report() -> dict[str, Any]:
    if not READINESS_REPORT_PATH.exists():
        return {
            "protocol_version": "1.0",
            "release": "v39-chaos-readiness",
            "status": "needs_validation",
            "reason": "run scripts/run_v39_operational_acceptance.py",
        }
    try:
        return json.loads(READINESS_REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "needs_validation", "reason": "invalid_readiness_report"}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
