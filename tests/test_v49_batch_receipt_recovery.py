from __future__ import annotations

from pathlib import Path

import pytest

from app.access.models import default_principal
from app.copilot.batch_execution import BatchExecutionReport
from app.copilot.batch_jobs import BatchExecutionDispatcher, BatchExecutionJobRequest
from app.copilot_ui import COPILOT_HTML
from app.distributed.runtime import DistributedRuntime
from app.main import app


def _request(
    *,
    batch_job_id: str = "batch_v49_demo",
    generation: int = 0,
    tenant_id: str = "tenant_demo",
) -> BatchExecutionJobRequest:
    principal = default_principal().model_copy(update={"tenant_id": tenant_id})
    return BatchExecutionJobRequest(
        batch_job_id=batch_job_id,
        operation="approve",
        item_ids=["item_01"],
        expected_checkpoint_versions={"item_01": 7 + generation},
        execution_generations={"item_01": generation},
        principal=principal,
    )


def _report(request: BatchExecutionJobRequest) -> BatchExecutionReport:
    return BatchExecutionReport(
        batch_job_id=request.batch_job_id,
        status="completed",
        selected_item_ids=request.item_ids,
        items=[],
        executed_count=1,
        failed_count=0,
        remaining_approval_count=0,
        assistant_message="批次执行完成。",
    )


def _dispatcher(path: Path) -> BatchExecutionDispatcher:
    return BatchExecutionDispatcher(
        DistributedRuntime(path, tenant_rate_per_minute=100)
    )


def test_latest_receipt_survives_dispatcher_reconstruction(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    first = _dispatcher(database)
    dispatch = first.enqueue(_request(), client_request_id="browser_before_reload")

    after_reload = _dispatcher(database)
    latest = after_reload.latest("batch_v49_demo", tenant_id="tenant_demo")

    assert latest is not None
    assert latest.runtime_job_id == dispatch.runtime_job_id
    assert latest.status == "queued"
    assert latest.is_latest is True
    assert latest.execution_generations == {"item_01": 0}


def test_old_receipt_cannot_overwrite_new_execution_generation(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path / "runtime.db")
    old = dispatcher.enqueue(_request(generation=0), client_request_id="old")
    new = dispatcher.enqueue(_request(generation=1), client_request_id="new")

    old_status = dispatcher.status(old.runtime_job_id, tenant_id="tenant_demo")
    new_status = dispatcher.status(new.runtime_job_id, tenant_id="tenant_demo")
    latest = dispatcher.latest("batch_v49_demo", tenant_id="tenant_demo")

    assert old_status.is_latest is False
    assert new_status.is_latest is True
    assert latest is not None and latest.runtime_job_id == new.runtime_job_id
    assert latest.execution_generations == {"item_01": 1}


def test_completed_result_can_be_recovered_after_disconnect(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    dispatcher = _dispatcher(database)
    dispatch = dispatcher.enqueue(_request(), client_request_id="disconnect")
    dispatcher.run_once(worker_id="browser_worker", executor=_report)

    recovered = _dispatcher(database).latest(
        "batch_v49_demo", tenant_id="tenant_demo"
    )

    assert recovered is not None
    assert recovered.runtime_job_id == dispatch.runtime_job_id
    assert recovered.status == "completed"
    assert recovered.result is not None
    assert recovered.result.assistant_message == "批次执行完成。"


def test_latest_receipt_is_tenant_scoped(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path / "runtime.db")
    dispatcher.enqueue(_request(), client_request_id="tenant_demo")

    assert dispatcher.latest("batch_v49_demo", tenant_id="tenant_other") is None
    with pytest.raises(KeyError):
        runtime_job_id = dispatcher.latest(
            "batch_v49_demo", tenant_id="tenant_demo"
        ).runtime_job_id
        dispatcher.status(runtime_job_id, tenant_id="tenant_other")


def test_runtime_receipt_prefix_is_literal_not_wildcard(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path / "runtime.db")
    expected = dispatcher.enqueue(
        _request(batch_job_id="batch_under_score"), client_request_id="expected"
    )
    dispatcher.enqueue(
        _request(batch_job_id="batchXunderXscore"), client_request_id="other"
    )

    latest = dispatcher.latest("batch_under_score", tenant_id="tenant_demo")

    assert latest is not None
    assert latest.runtime_job_id == expected.runtime_job_id


def test_v49_recovery_api_and_browser_protocol_are_exposed() -> None:
    paths = {route.path for route in app.routes}

    assert "/api/copilot/batches/{batch_job_id}/executions/latest" in paths
    assert "resumeLatestBatchExecution" in COPILOT_HTML
    assert "status.is_latest" in COPILOT_HTML
    assert "invalidateBatchRecovery" in COPILOT_HTML
