from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.distributed.bulkhead import BulkheadFullError, BulkheadRegistry
from app.distributed.runtime import (
    DistributedRuntime,
    OptimisticVersionConflict,
    QueueBackpressureError,
    ResourceBusyError,
    RuntimeIdempotencyConflict,
    StaleFencingTokenError,
    StaleLeaseError,
)


def runtime(tmp_path: Path, **kwargs) -> DistributedRuntime:
    return DistributedRuntime(
        tmp_path / "runtime.db",
        global_queue_limit=kwargs.pop("global_queue_limit", 50),
        tenant_queue_limit=kwargs.pop("tenant_queue_limit", 20),
        tenant_rate_per_minute=kwargs.pop("tenant_rate_per_minute", 100),
        lease_seconds=kwargs.pop("lease_seconds", 30),
        **kwargs,
    )


def enqueue(store: DistributedRuntime, tenant: str, key: str, value: int = 1):
    return store.enqueue(
        tenant_id=tenant,
        pool="workflow",
        job_type="demo",
        idempotency_key=key,
        payload={"value": value},
        enforce_rate_limit=False,
    )


def test_enqueue_is_durable_idempotent_and_rejects_key_reuse(tmp_path: Path) -> None:
    store = runtime(tmp_path)
    first, replayed = enqueue(store, "tenant_a", "request_1")
    second, replayed_second = enqueue(store, "tenant_a", "request_1")

    assert replayed is False
    assert replayed_second is True
    assert second.job_id == first.job_id
    with pytest.raises(RuntimeIdempotencyConflict):
        enqueue(store, "tenant_a", "request_1", value=2)
    assert DistributedRuntime(tmp_path / "runtime.db").get_job(first.job_id) == first


def test_backpressure_is_per_tenant_and_does_not_block_other_tenants(tmp_path: Path) -> None:
    store = runtime(tmp_path, tenant_queue_limit=2)
    enqueue(store, "tenant_a", "a1")
    enqueue(store, "tenant_a", "a2")
    with pytest.raises(QueueBackpressureError):
        enqueue(store, "tenant_a", "a3")
    other, _ = enqueue(store, "tenant_b", "b1")
    assert other.tenant_id == "tenant_b"


def test_fair_dispatch_serves_waiting_tenants_before_one_tenant_drains(tmp_path: Path) -> None:
    store = runtime(tmp_path)
    enqueue(store, "tenant_a", "a1")
    enqueue(store, "tenant_a", "a2")
    enqueue(store, "tenant_b", "b1")

    first = store.lease_next(worker_id="worker", pool="workflow")
    assert first is not None and first.job.tenant_id == "tenant_a"
    store.complete(first, {"ok": True})
    second = store.lease_next(worker_id="worker", pool="workflow")
    assert second is not None and second.job.tenant_id == "tenant_b"


def test_expired_worker_is_reclaimed_and_stale_completion_is_fenced(tmp_path: Path) -> None:
    store = runtime(tmp_path)
    enqueue(store, "tenant_a", "job")
    old = store.lease_next(worker_id="worker_old", pool="workflow")
    assert old is not None
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE runtime_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
            (old.job.job_id,),
        )
    with pytest.raises(StaleLeaseError):
        store.heartbeat(old)
    new = store.lease_next(worker_id="worker_new", pool="workflow")
    assert new is not None
    assert new.lease_token > old.lease_token
    with pytest.raises(StaleLeaseError):
        store.complete(old, {"winner": "old"})
    completed = store.complete(new, {"winner": "new"})
    assert completed.result == {"winner": "new"}


def test_resource_lock_fence_version_saga_and_outbox_commit_together(tmp_path: Path) -> None:
    store = runtime(tmp_path)
    plan = {"operation": "update_listing", "product_id": "p1", "price": 199}
    permit = store.prepare_execution(
        tenant_id="tenant_a",
        resource_id="product:p1",
        operation="update_listing",
        plan=plan,
        owner_id="worker_a",
    )
    with pytest.raises(ResourceBusyError):
        store.prepare_execution(
            tenant_id="tenant_a",
            resource_id="product:p1",
            operation="update_listing",
            plan={**plan, "price": 200},
            owner_id="worker_b",
        )
    store.mark_execution_started(permit)
    effect, replayed = store.confirm_execution(permit, {"status": "verified"})

    assert replayed is False
    assert effect.resource_version == 1
    assert store.sagas(tenant_id="tenant_a")[0].status == "completed"
    snapshot = store.snapshot(tenant_id="tenant_a")
    assert snapshot["confirmed_business_effects"] == 1
    assert snapshot["pending_outbox_events"] == 2
    outbox = store.lease_outbox(publisher_id="publisher")
    assert outbox is not None and outbox["status"] == "publishing"
    store.mark_outbox_published(
        outbox_id=outbox["outbox_id"],
        publisher_id="publisher",
        lease_token=outbox["lease_token"],
    )
    assert store.snapshot(tenant_id="tenant_a")["pending_outbox_events"] == 1


def test_repeated_execution_plan_has_one_confirmed_business_effect(tmp_path: Path) -> None:
    store = runtime(tmp_path)
    plan = {"operation": "update_listing", "product_id": "p1", "price": 199}
    first = store.prepare_execution(
        tenant_id="tenant_a", resource_id="product:p1",
        operation="update_listing", plan=plan, owner_id="worker_a",
    )
    effect, _ = store.confirm_execution(first, {"status": "verified"})
    replay = store.prepare_execution(
        tenant_id="tenant_a", resource_id="product:p1",
        operation="update_listing", plan=plan, owner_id="worker_b",
    )
    replay_effect, replayed = store.confirm_execution(replay, {"ignored": True})

    assert replay.replay_result == {"status": "verified"}
    assert replayed is True
    assert replay_effect.effect_id == effect.effect_id
    assert store.snapshot(tenant_id="tenant_a")["confirmed_business_effects"] == 1


def test_stale_fencing_token_cannot_commit_after_lease_takeover(tmp_path: Path) -> None:
    store = runtime(tmp_path)
    first = store.prepare_execution(
        tenant_id="tenant_a", resource_id="product:p1", operation="update_listing",
        plan={"price": 199}, owner_id="worker_old",
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """UPDATE resource_guards SET lease_expires_at='2000-01-01T00:00:00+00:00'
            WHERE tenant_id='tenant_a' AND resource_id='product:p1'"""
        )
    second = store.prepare_execution(
        tenant_id="tenant_a", resource_id="product:p1", operation="update_listing",
        plan={"price": 200}, owner_id="worker_new",
    )
    assert second.fencing_token > first.fencing_token
    with pytest.raises(StaleFencingTokenError):
        store.confirm_execution(first, {"status": "old"})
    store.confirm_execution(second, {"status": "new"})
    assert {item.status for item in store.sagas(tenant_id="tenant_a")} == {
        "completed", "needs_attention"
    }


def test_optimistic_version_conflict_is_explicit(tmp_path: Path) -> None:
    store = runtime(tmp_path)
    permit = store.prepare_execution(
        tenant_id="tenant_a", resource_id="product:p1", operation="update_listing",
        plan={"price": 199}, owner_id="worker",
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE resource_guards SET resource_version=5 WHERE tenant_id='tenant_a' AND resource_id='product:p1'"
        )
    with pytest.raises(OptimisticVersionConflict):
        store.confirm_execution(permit, {"status": "invalid"})


def test_bulkheads_isolate_browser_saturation_from_sql(tmp_path: Path) -> None:
    bulkheads = BulkheadRegistry({"browser": 1, "sql": 1})
    with bulkheads.acquire("browser"):
        with pytest.raises(BulkheadFullError):
            with bulkheads.acquire("browser"):
                pass
        with bulkheads.acquire("sql"):
            assert bulkheads.snapshot()["sql"]["active"] == 1


def test_concurrent_duplicate_submission_creates_one_job(tmp_path: Path) -> None:
    store = runtime(tmp_path)

    def submit(_index: int) -> str:
        return enqueue(store, "tenant_a", "same-click")[0].job_id

    with ThreadPoolExecutor(max_workers=12) as executor:
        job_ids = list(executor.map(submit, range(24)))
    assert len(set(job_ids)) == 1
