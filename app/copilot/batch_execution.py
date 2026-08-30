from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.access.models import AccessPrincipal
from app.conversations.repository import (
    ConversationConflictError,
    ConversationNotFoundError,
    ConversationRepository,
)
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotResponse
from app.copilot.schemas import CopilotOutcome, PanelDescriptor
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import failure_from_exception


class BatchExecutionItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    label: str
    task_id: str | None = None
    status: Literal["completed", "failed", "skipped"]
    store_modified: bool = False
    assistant_message: str
    error_code: str | None = None
    entity_refs: list[str] = Field(default_factory=list)
    execution_attempts: int = Field(default=0, ge=0, le=3)


class BatchExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    batch_job_id: str
    status: Literal[
        "awaiting_approval", "partially_completed", "completed", "failed"
    ]
    selected_item_ids: list[str]
    items: list[BatchExecutionItemResult]
    executed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    remaining_approval_count: int = Field(ge=0)
    assistant_message: str


ApproveCallable = Callable[
    [str, AccessPrincipal, int | None],
    CopilotResponse,
]


class BatchExecutionService:
    """Serialize selected destructive writes while retaining child-level evidence."""

    def __init__(
        self,
        repository: ConversationRepository | None = None,
        *,
        approver: ApproveCallable | None = None,
    ) -> None:
        self._repository = repository or ConversationRepository()
        self._approver = approver or self._approve_task

    def execute(
        self,
        batch_job_id: str,
        *,
        principal: AccessPrincipal,
        item_ids: list[str],
        expected_checkpoint_versions: dict[str, int] | None = None,
        retry_failed: bool = False,
    ) -> BatchExecutionReport:
        selected = list(dict.fromkeys(item_ids))
        if not selected or len(selected) > 5:
            raise ValueError("Select between 1 and 5 batch items")
        if len(selected) != len(item_ids):
            raise ValueError("Duplicate batch item selections are not allowed")
        batch = self._repository.get_batch_job(principal.tenant_id, batch_job_id)
        records = self._repository.list_batch_items(
            principal.tenant_id, batch.batch_job_id
        )
        by_id = {item.item_id: item for item in records}
        unknown = [item_id for item_id in selected if item_id not in by_id]
        if unknown:
            raise ConversationNotFoundError(
                "Selected batch items do not belong to this batch"
            )

        versions = expected_checkpoint_versions or {}
        results: list[BatchExecutionItemResult] = []
        # Deliberately sequential: browser/store writes are destructive and must
        # not race just because plan generation was allowed bounded concurrency.
        for item_id in selected:
            record = by_id[item_id]
            if record.status == "completed":
                results.append(self._completed_result(record))
                continue
            if record.status == "needs_attention" and retry_failed:
                state = CheckpointStore().load(record.task_id)
                unknown_writes = [
                    tool_record
                    for tool_record in state.tool_records
                    if tool_record.get("status") == "unknown"
                    and tool_record.get("side_effect")
                ]
                if unknown_writes:
                    results.append(
                        BatchExecutionItemResult(
                            item_id=record.item_id,
                            label=record.label,
                            task_id=record.task_id,
                            status="failed",
                            assistant_message=(
                                "上次写入结果未知，必须先通过店铺回读对账，不能直接重试。"
                            ),
                            error_code="batch_unknown_write_requires_reconciliation",
                            execution_attempts=record.execution_attempts,
                        )
                    )
                    continue
                if record.execution_attempts >= 3:
                    results.append(
                        BatchExecutionItemResult(
                            item_id=record.item_id,
                            label=record.label,
                            task_id=record.task_id,
                            status="failed",
                            assistant_message="该商品已达到 3 次执行上限，需要人工检查。",
                            error_code="batch_retry_exhausted",
                            execution_attempts=record.execution_attempts,
                        )
                    )
                    continue
                record = self._repository.prepare_batch_item_retry(
                    principal.tenant_id, batch_job_id, item_id
                )
            if record.status != "awaiting_approval" or not record.task_id:
                results.append(
                    BatchExecutionItemResult(
                        item_id=record.item_id,
                        label=record.label,
                        task_id=record.task_id,
                        status="skipped",
                        assistant_message=(
                            "该商品当前不处于等待确认状态，没有执行重复或越级写入。"
                        ),
                        error_code="batch_item_not_awaiting_approval",
                        execution_attempts=record.execution_attempts,
                    )
                )
                continue

            self._repository.claim_batch_item_execution(
                principal.tenant_id, batch_job_id, item_id
            )
            try:
                state = CheckpointStore().load(record.task_id)
                if state.principal.tenant_id != principal.tenant_id:
                    raise PermissionError("Batch child tenant does not match requester")
                expected = versions.get(item_id)
                if expected is not None and expected != state.checkpoint_version:
                    raise ConversationConflictError(
                        "The child plan changed after batch confirmation"
                    )
                response = self._approver(record.task_id, principal, expected)
                if not response.store_modified:
                    raise RuntimeError(
                        "Child approval returned without a verified store modification"
                    )
                self._repository.record_batch_item_execution(
                    principal.tenant_id,
                    batch_job_id,
                    item_id,
                    completed=True,
                )
                updated = self._item(principal.tenant_id, batch_job_id, item_id)
                results.append(
                    BatchExecutionItemResult(
                        item_id=record.item_id,
                        label=record.label,
                        task_id=record.task_id,
                        status="completed",
                        store_modified=True,
                        assistant_message=response.assistant_message,
                        entity_refs=list(response.entity_refs),
                        execution_attempts=updated.execution_attempts,
                    )
                )
            except Exception as exc:
                failure = failure_from_exception(
                    exc,
                    stage="batch_child_execution",
                    agent_name="batch_execution_service",
                )
                self._repository.record_batch_item_execution(
                    principal.tenant_id,
                    batch_job_id,
                    item_id,
                    completed=False,
                    error_code=failure.code,
                )
                updated = self._item(principal.tenant_id, batch_job_id, item_id)
                results.append(
                    BatchExecutionItemResult(
                        item_id=record.item_id,
                        label=record.label,
                        task_id=record.task_id,
                        status="failed",
                        assistant_message=failure.user_message,
                        error_code=failure.code,
                        execution_attempts=updated.execution_attempts,
                    )
                )

        aggregate = self._repository.finalize_batch_execution(
            principal.tenant_id, batch_job_id
        )
        current_items = self._repository.list_batch_items(
            principal.tenant_id, batch_job_id
        )
        remaining = sum(item.status == "awaiting_approval" for item in current_items)
        executed = sum(item.status == "completed" for item in current_items)
        failed = sum(
            item.status in {"failed", "skipped", "needs_attention"}
            for item in current_items
        )
        message = (
            f"本次选择 {len(selected)} 个商品，成功同步 "
            f"{sum(item.status == 'completed' for item in results)} 个，"
            f"失败 {sum(item.status == 'failed' for item in results)} 个。"
        )
        if remaining:
            message += f"还有 {remaining} 个商品方案等待你确认。"
        report = BatchExecutionReport(
            batch_job_id=batch_job_id,
            status=aggregate.status,
            selected_item_ids=selected,
            items=results,
            executed_count=executed,
            failed_count=failed,
            remaining_approval_count=remaining,
            assistant_message=message,
        )
        self._persist_response_snapshot(
            principal.tenant_id, batch, current_items, report
        )
        return report

    def _approve_task(
        self,
        task_id: str,
        principal: AccessPrincipal,
        expected_checkpoint_version: int | None,
    ) -> CopilotResponse:
        return ConversationFacade(repository=self._repository).approve(
            task_id,
            principal=principal,
            expected_checkpoint_version=expected_checkpoint_version,
            reason="用户明确确认批次中的选定商品",
        )

    @staticmethod
    def _completed_result(record) -> BatchExecutionItemResult:
        state = CheckpointStore().load(record.task_id)
        return BatchExecutionItemResult(
            item_id=record.item_id,
            label=record.label,
            task_id=record.task_id,
            status="completed",
            store_modified=True,
            assistant_message="该商品此前已经同步，幂等复用原执行结果。",
            entity_refs=list(state.entity_refs),
            execution_attempts=record.execution_attempts,
        )

    def _item(self, tenant_id: str, batch_job_id: str, item_id: str):
        return next(
            item
            for item in self._repository.list_batch_items(tenant_id, batch_job_id)
            if item.item_id == item_id
        )

    def _persist_response_snapshot(
        self,
        tenant_id: str,
        batch,
        current_items,
        report: BatchExecutionReport,
    ) -> None:
        payload = self._repository.response_for_turn(
            tenant_id, batch.origin_turn_id
        )
        if not payload:
            return
        response = CopilotResponse.model_validate(payload)
        item_status = {item.item_id: item for item in current_items}
        for panel in response.panels:
            if (
                panel.panel_id != "requirements"
                or panel.data.get("batch_job_id") != report.batch_job_id
            ):
                continue
            values = list(panel.data.get("items") or [])
            panel.data["items"] = [
                {
                    **value,
                    "status": item_status[value["item_id"]].status,
                    "error_code": item_status[value["item_id"]].error_code,
                    "execution_attempts": item_status[value["item_id"]].execution_attempts,
                    "execution_history": item_status[value["item_id"]].execution_history,
                }
                for value in values
                if value.get("item_id") in item_status
            ]
            panel.data["batch_status"] = report.status
            panel.summary = report.assistant_message
        execution_panel = PanelDescriptor(
            panel_id="execution",
            title="批次店铺同步结果",
            status=(
                "completed"
                if report.status == "completed"
                else "failed"
                if report.status == "failed"
                else "ready"
            ),
            summary=report.assistant_message,
            data={"batch_execution": report.model_dump(mode="json")},
            source_agents=["batch_execution_service"],
        )
        response.panels = [
            panel for panel in response.panels if panel.panel_id != "execution"
        ]
        response.panels.append(execution_panel)
        response.assistant_message = report.assistant_message
        response.store_modified = report.executed_count > 0
        response.outcome = (
            CopilotOutcome.completed
            if report.status in {"completed", "partially_completed"}
            else CopilotOutcome.awaiting_approval
            if report.status == "awaiting_approval"
            else CopilotOutcome.technical_failed
        )
        response.action_summary.headline = report.assistant_message
        by_result = {item.item_id: item for item in report.items}
        for step in response.action_summary.steps:
            result = by_result.get(step.step_id)
            if result is None:
                continue
            step.status = "completed" if result.status == "completed" else "failed"
            step.detail = result.assistant_message
        response.action_summary.completed_step_count = sum(
            step.status == "completed" for step in response.action_summary.steps
        )
        response.action_summary.execution_performed = report.executed_count > 0
        self._repository.update_batch_response_snapshot(
            tenant_id,
            report.batch_job_id,
            response_payload=response.model_dump(mode="json"),
            assistant_message=report.assistant_message,
        )
