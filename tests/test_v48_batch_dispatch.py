from __future__ import annotations

from pathlib import Path

import pytest

from app.access.models import default_principal
from app.copilot.batch_execution import BatchExecutionReport
from app.copilot.batch_jobs import BatchExecutionDispatcher, BatchExecutionJobRequest
from app.distributed.runtime import DistributedRuntime, RuntimeIdempotencyConflict
from app.main import app


def _request(
    *, item_ids: list[str] | None = None, checkpoint_version: int = 7
) -> BatchExecutionJobRequest:
    return BatchExecutionJobRequest(
        batch_job_id="batch_v48_demo",
        operation="approve",
        item_ids=item_ids or ["item_01"],
        expected_checkpoint_versions={"item_01": checkpoint_version},
        execution_generations={item_id: 0 for item_id in (item_ids or ["item_01"])},
        principal=default_principal(),
    )


def _report(request: BatchExecutionJobRequest) -> BatchExecutionReport:
    return BatchExecutionReport(
        batch_job_id=request.batch_job_id,
        status="completed",
        selected_item_ids=request.item_ids,
        items=[],
        executed_count=len(request.item_ids),
        failed_count=0,
        remaining_approval_count=0,
        assistant_message="批次店铺同步完成。",
    )


def test_dispatch_is_durable_idempotent_and_queryable(tmp_path: Path) -> None:
    runtime = DistributedRuntime(
        tmp_path / "runtime.db",
        global_queue_limit=20,
        tenant_queue_limit=10,
        tenant_rate_per_minute=100,
    )
    dispatcher = BatchExecutionDispatcher(runtime)
    request = _request()
    first = dispatcher.enqueue(request, client_request_id="click_001")
    replay = dispatcher.enqueue(request, client_request_id="click_after_refresh")

    assert first.runtime_job_id == replay.runtime_job_id
    assert replay.replayed is True
    queued = dispatcher.status(first.runtime_job_id, tenant_id="tenant_demo")
    assert queued.status == "queued"

    finished = dispatcher.run_once(worker_id="browser_worker_1", executor=_report)
    status = dispatcher.status(first.runtime_job_id, tenant_id="tenant_demo")

    assert finished is not None and finished.status == "completed"
    assert status.status == "completed"
    assert status.result is not None
    assert status.result.executed_count == 1
    assert status.attempts == 1


def test_same_logical_generation_rejects_changed_checkpoint_payload(tmp_path: Path) -> None:
    dispatcher = BatchExecutionDispatcher(
        DistributedRuntime(tmp_path / "runtime.db", tenant_rate_per_minute=100)
    )
    dispatcher.enqueue(_request(), client_request_id="same_click")

    with pytest.raises(RuntimeIdempotencyConflict):
        dispatcher.enqueue(
            _request(checkpoint_version=8),
            client_request_id="new_click",
        )


def test_new_execution_generation_gets_a_new_runtime_job(tmp_path: Path) -> None:
    dispatcher = BatchExecutionDispatcher(
        DistributedRuntime(tmp_path / "runtime.db", tenant_rate_per_minute=100)
    )
    first_request = _request()
    second_request = first_request.model_copy(
        update={"execution_generations": {"item_01": 1}}
    )

    first = dispatcher.enqueue(first_request, client_request_id="retry_1")
    second = dispatcher.enqueue(second_request, client_request_id="retry_2")

    assert first.runtime_job_id != second.runtime_job_id


def test_worker_failure_retries_then_moves_job_to_dead(tmp_path: Path) -> None:
    dispatcher = BatchExecutionDispatcher(
        DistributedRuntime(tmp_path / "runtime.db", tenant_rate_per_minute=100)
    )
    dispatch = dispatcher.enqueue(_request(), client_request_id="dead_job")

    def fail(_request):
        raise RuntimeError("database unavailable")

    statuses = [
        dispatcher.run_once(worker_id=f"worker_{index}", executor=fail).status
        for index in range(3)
    ]
    status = dispatcher.status(dispatch.runtime_job_id, tenant_id="tenant_demo")

    assert statuses == ["queued", "queued", "dead"]
    assert status.status == "dead"
    assert status.attempts == status.max_attempts == 3
    assert "database unavailable" in (status.error or "")


def test_batch_execution_job_status_is_tenant_scoped(tmp_path: Path) -> None:
    dispatcher = BatchExecutionDispatcher(
        DistributedRuntime(tmp_path / "runtime.db", tenant_rate_per_minute=100)
    )
    dispatch = dispatcher.enqueue(_request(), client_request_id="tenant_scope")

    with pytest.raises(KeyError):
        dispatcher.status(dispatch.runtime_job_id, tenant_id="tenant_other")


def test_v48_dispatch_and_status_apis_are_exposed() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/copilot/batches/{batch_job_id}/dispatch" in paths
    assert "/api/copilot/batch-executions/{runtime_job_id}" in paths
