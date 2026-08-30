from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.conversations.models import BatchItemRecord
from app.conversations.repository import ConversationRepository
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import failure_from_exception
from app.orchestration.state import TaskState


class BatchItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    label: str
    task_session_id: str
    status: Literal["awaiting_approval", "completed", "failed", "skipped"]
    task_id: str | None = None
    run_id: str | None = None
    checkpoint_version: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    tool_call_count: int = Field(default=0, ge=0)
    model_records: list[dict] = Field(default_factory=list)


class BatchRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_job_id: str
    status: Literal["awaiting_approval", "partially_completed", "failed"]
    items: list[BatchItemResult]
    successful_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)


class BoundedBatchOrchestrator:
    """Run independent child plans with a small, explicit concurrency bound."""

    def __init__(
        self,
        repository: ConversationRepository,
        *,
        max_workers: int = 2,
    ) -> None:
        self._repository = repository
        self._max_workers = max(1, min(max_workers, 2))

    def run(
        self,
        tenant_id: str,
        batch_job_id: str,
        runner: Callable[[BatchItemRecord], TaskState],
    ) -> BatchRunReport:
        records = self._repository.list_batch_items(tenant_id, batch_job_id)
        if not 2 <= len(records) <= 5:
            raise ValueError("A runnable batch must contain 2 to 5 items")

        results: list[BatchItemResult] = []
        pending: list[BatchItemRecord] = []
        for record in records:
            reused = self._reuse_completed_item(record)
            if reused is not None:
                results.append(reused)
            elif record.status == "waiting_for_input":
                self._repository.record_batch_item_skipped(
                    tenant_id,
                    record.batch_job_id,
                    record.item_id,
                    error_code="missing_required_fields",
                )
                results.append(
                    BatchItemResult(
                        item_id=record.item_id,
                        label=record.label,
                        task_session_id=record.task_session_id,
                        status="skipped",
                        error_code="missing_required_fields",
                        error_message="该商品仍缺少上架所需字段。",
                    )
                )
            else:
                pending.append(record)

        with ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(pending) or 1),
            thread_name_prefix="ecompilot-batch",
        ) as executor:
            future_map = {
                executor.submit(self._run_one, tenant_id, record, runner): record
                for record in pending
            }
            for future in as_completed(future_map):
                results.append(future.result())

        order = {record.item_id: index for index, record in enumerate(records)}
        results.sort(key=lambda item: order[item.item_id])
        successful = sum(
            item.status in {"awaiting_approval", "completed"} for item in results
        )
        failed = sum(item.status in {"failed", "skipped"} for item in results)
        status = (
            "failed"
            if successful == 0
            else "partially_completed"
            if failed
            else "awaiting_approval"
        )
        self._repository.finalize_batch_job(tenant_id, batch_job_id, status=status)
        return BatchRunReport(
            batch_job_id=batch_job_id,
            status=status,
            items=results,
            successful_count=successful,
            failed_count=failed,
        )

    def _run_one(
        self,
        tenant_id: str,
        record: BatchItemRecord,
        runner: Callable[[BatchItemRecord], TaskState],
    ) -> BatchItemResult:
        self._repository.mark_batch_item_running(
            tenant_id, record.batch_job_id, record.item_id
        )
        try:
            state = runner(record)
            status = (
                "completed" if state.outcome.value == "completed" else "awaiting_approval"
            )
            self._repository.record_batch_item_state(
                state,
                batch_job_id=record.batch_job_id,
                item_id=record.item_id,
                task_session_id=record.task_session_id,
                status=status,
            )
            return BatchItemResult(
                item_id=record.item_id,
                label=record.label,
                task_session_id=record.task_session_id,
                status=status,
                task_id=state.task_id,
                run_id=state.run_id,
                checkpoint_version=state.checkpoint_version,
                tool_call_count=len(state.tool_records),
                model_records=list(state.model_records),
            )
        except Exception as exc:
            failure = failure_from_exception(
                exc, stage="batch_item_workflow", agent_name="batch_orchestrator"
            )
            self._repository.record_batch_item_failure(
                tenant_id,
                record.batch_job_id,
                record.item_id,
                error_code=failure.code,
            )
            return BatchItemResult(
                item_id=record.item_id,
                label=record.label,
                task_session_id=record.task_session_id,
                status="failed",
                error_code=failure.code,
                error_message=failure.user_message,
            )

    @staticmethod
    def _reuse_completed_item(record: BatchItemRecord) -> BatchItemResult | None:
        if not record.task_id or record.status not in {"awaiting_approval", "completed"}:
            return None
        try:
            state = CheckpointStore().load(record.task_id)
        except Exception:
            return None
        return BatchItemResult(
            item_id=record.item_id,
            label=record.label,
            task_session_id=record.task_session_id,
            status=record.status,
            task_id=state.task_id,
            run_id=state.run_id,
            checkpoint_version=state.checkpoint_version,
            tool_call_count=len(state.tool_records),
            model_records=list(state.model_records),
        )
