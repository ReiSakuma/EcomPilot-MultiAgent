from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.distributed.bulkhead import BulkheadFullError, BulkheadRegistry
from app.distributed.runtime import (
    DistributedRuntime,
    ResourceBusyError,
    StaleFencingTokenError,
    StaleLeaseError,
)


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="ecompilot-v38-") as directory:
        runtime = DistributedRuntime(
            Path(directory) / "runtime.db",
            global_queue_limit=100,
            tenant_queue_limit=50,
            tenant_rate_per_minute=1000,
            lease_seconds=30,
        )

        def submit(_index: int) -> str:
            job, _ = runtime.enqueue(
                tenant_id="tenant_demo", pool="workflow", job_type="acceptance",
                idempotency_key="same-user-click", payload={"task": "listing"},
                enforce_rate_limit=False,
            )
            return job.job_id

        with ThreadPoolExecutor(max_workers=16) as executor:
            duplicate_jobs = list(executor.map(submit, range(40)))

        runtime.enqueue(
            tenant_id="tenant_alpha", pool="workflow", job_type="acceptance",
            idempotency_key="alpha-1", payload={}, enforce_rate_limit=False,
        )
        runtime.enqueue(
            tenant_id="tenant_alpha", pool="workflow", job_type="acceptance",
            idempotency_key="alpha-2", payload={}, enforce_rate_limit=False,
        )
        runtime.enqueue(
            tenant_id="tenant_beta", pool="workflow", job_type="acceptance",
            idempotency_key="beta-1", payload={}, enforce_rate_limit=False,
        )
        fair_first = runtime.lease_next(worker_id="fair-worker", pool="workflow")
        runtime.complete(fair_first, {"ok": True})
        fair_second = runtime.lease_next(worker_id="fair-worker", pool="workflow")

        lost_job, _ = runtime.enqueue(
            tenant_id="tenant_loss", pool="sql", job_type="acceptance",
            idempotency_key="worker-loss", payload={}, enforce_rate_limit=False,
        )
        old = runtime.lease_next(worker_id="worker-old", pool="sql")
        with sqlite3.connect(runtime.database_path) as connection:
            connection.execute(
                "UPDATE runtime_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
                (lost_job.job_id,),
            )
        new = runtime.lease_next(worker_id="worker-new", pool="sql")
        stale_job_blocked = False
        try:
            runtime.complete(old, {"worker": "old"})
        except StaleLeaseError:
            stale_job_blocked = True
        recovered = runtime.complete(new, {"worker": "new"})

        plan = {"product_id": "p38", "price": 299, "operation": "update_listing"}
        first_permit = runtime.prepare_execution(
            tenant_id="tenant_demo", resource_id="product:p38",
            operation="update_listing", plan=plan, owner_id="writer-a",
        )
        conflict_blocked = False
        try:
            runtime.prepare_execution(
                tenant_id="tenant_demo", resource_id="product:p38",
                operation="update_listing", plan={**plan, "price": 300}, owner_id="writer-b",
            )
        except ResourceBusyError:
            conflict_blocked = True
        effect, _ = runtime.confirm_execution(first_permit, {"verified": True})
        replay = runtime.prepare_execution(
            tenant_id="tenant_demo", resource_id="product:p38",
            operation="update_listing", plan=plan, owner_id="writer-replay",
        )
        replay_effect, replayed = runtime.confirm_execution(replay, {"ignored": True})

        stale_permit = runtime.prepare_execution(
            tenant_id="tenant_demo", resource_id="product:p39",
            operation="update_listing", plan={"price": 100}, owner_id="stale-writer",
        )
        with sqlite3.connect(runtime.database_path) as connection:
            connection.execute(
                "UPDATE resource_guards SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE tenant_id='tenant_demo' AND resource_id='product:p39'"
            )
        current_permit = runtime.prepare_execution(
            tenant_id="tenant_demo", resource_id="product:p39",
            operation="update_listing", plan={"price": 101}, owner_id="current-writer",
        )
        stale_effect_blocked = False
        try:
            runtime.confirm_execution(stale_permit, {"price": 100})
        except StaleFencingTokenError:
            stale_effect_blocked = True
        runtime.confirm_execution(current_permit, {"price": 101})

        bulkheads = BulkheadRegistry({"browser": 1, "sql": 1})
        browser_isolated = False
        with bulkheads.acquire("browser"):
            try:
                with bulkheads.acquire("browser"):
                    pass
            except BulkheadFullError:
                with bulkheads.acquire("sql"):
                    browser_isolated = True

        snapshot = runtime.snapshot(tenant_id="tenant_demo")
        scenarios = {
            "repeated_click_one_job": len(set(duplicate_jobs)) == 1,
            "tenant_fair_dispatch": fair_first.job.tenant_id != fair_second.job.tenant_id,
            "worker_loss_reclaimed": new.lease_token > old.lease_token,
            "stale_worker_commit_blocked": stale_job_blocked,
            "recovered_worker_completed": recovered.result == {"worker": "new"},
            "same_resource_conflict_blocked": conflict_blocked,
            "same_plan_one_effect": replayed and replay_effect.effect_id == effect.effect_id,
            "stale_resource_fence_blocked": stale_effect_blocked,
            "saga_outbox_transaction_visible": snapshot["pending_outbox_events"] >= 4,
            "bulkhead_failure_isolated": browser_isolated,
        }
        return {
            "version": "v38-concurrency-control",
            "passed": all(scenarios.values()),
            "scenarios": scenarios,
            "checks_passed": sum(scenarios.values()),
            "checks_total": len(scenarios),
            "duplicate_writes": snapshot["confirmed_business_effects"] - 2,
            "runtime": snapshot,
        }


def main() -> None:
    report = run()
    output = ROOT / "reports" / "raw" / "v38_concurrency_acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
