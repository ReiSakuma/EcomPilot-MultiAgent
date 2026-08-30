from __future__ import annotations

import asyncio
import json
from datetime import datetime
from threading import Thread
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.access.audit import AccessAuditStore
from app.access.identity import (
    AuthenticationError,
    identity_catalog,
    resolve_principal,
)
from app.access.models import AccessPrincipal
from app.access.policy import AccessDeniedError, AccessPolicy
from app.config import (
    BROWSER_ARTIFACT_DIR,
    BROWSER_TICKET_TTL_SECONDS,
    IDEMPOTENCY_DIR,
    LLM_MODEL,
    LLM_PROVIDER,
    PROJECT_ROOT,
)
from app.access.context import tenant_scope
from app.agents.supervisor import Supervisor
from app.demo_ui import DEMO_HTML
from app.user_ui import USER_HTML
from app.trace_ui import TRACE_HTML
from app.observability.store import InvalidRunIdError, TraceNotFoundError, TraceStore
from app.eval.runner import run_eval
from app.eval.llm_eval import run_llm_eval
from app.eval.browser_eval import run_browser_eval
from app.eval.recovery_eval import run_recovery_eval
from app.model.adapter import ModelAdapter, ModelProviderError
from app.model.runtime import get_llm_runtime_status
from app.browser.runtime import get_browser_runtime_status
from app.linked_runtime import get_linked_runtime_status
from app.browser.service import (
    execute_ticketed_plan,
    observed_ticketed_product_state,
)
from app.browser.tickets import BrowserTicketError, BrowserTicketStore
from app.copilot.facade import ConversationFacade
from app.copilot.batch_execution import BatchExecutionReport, BatchExecutionService
from app.copilot.batch_jobs import (
    BatchExecutionDispatcher,
    BatchExecutionDispatch,
    BatchExecutionJobRequest,
    BatchExecutionJobStatus,
)
from app.copilot.compiler import RequestCompiler
from app.copilot.routing import ConversationOrchestrator
from app.copilot.events import CopilotEventStore
from app.public_progress import bind_public_event_sink
from app.copilot.schemas import ActiveStreamResponse, CopilotDispatchResponse, CopilotResponse
from app.copilot.schemas import ConversationDetailResponse, ConversationListResponse
from app.conversations.models import ConversationRecord
from app.conversations.repository import (
    ConversationConflictError,
    ConversationNotFoundError,
    ConversationRepository,
    ConversationStoreError,
)
from app.orchestration.checkpoint import (
    CheckpointError,
    CheckpointNotFoundError,
    CheckpointStore,
    InvalidTaskIdError,
    StaleCheckpointError,
)
from app.presentation import state_response
from app.orchestration.recovery import (
    RecoveryConflictError,
    RecoveryNotAllowedError,
    RecoveryValidationError,
)
from app.orchestration.a2a_inspection import (
    build_capability_catalog,
    build_task_collaboration_summary,
)
from app.safety.approval import Approval
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.ui import product_detail_html, seller_center_editor_html
from app.tools.browser_tools import (
    browser_execute,
    browser_verify,
    get_seller_center_snapshot,
    reset_seller_center,
)
from app.tools.registry import ToolRegistry
from app.sql.service import get_market_sql_service
from app.security.ledger import (
    SecurityLedger,
    SecurityLedgerError,
    build_task_security_summary,
)
from app.release.catalog import build_threat_model
from app.release.evidence import current_evidence_status, load_evidence_manifest
from app.release.readiness import build_release_readiness
from app.release.protocols import build_protocol_manifest
from app.release.final import build_final_release_status
from app.release.v59 import build_linkage_identity, build_v59_release_status
from app.reliability.dead_letter import get_dead_letter_store
from app.distributed.runtime import (
    DistributedRuntime,
    QueueBackpressureError,
    RuntimeIdempotencyConflict,
    TenantRateLimitError,
)
from app.operations.assessment import load_operational_report
from app.tools.registry import GLOBAL_CIRCUITS
from app.products.ledger import ProductLedger, ProductNotFoundError
from app.products.models import ProductDetail, ProductRecord
from app.memory.long_term import LongTermMemory, MerchantMemory
from app.memory.conversation import ConversationMemoryService, StructuredConversationSummary
from pathlib import Path


app = FastAPI(title="EcomPilot MultiAgent")
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")

AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]


def _principal(authorization: str | None) -> AccessPrincipal:
    try:
        return resolve_principal(authorization)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _authorize(
    principal: AccessPrincipal,
    action: str,
    *,
    resource_tenant_id: str | None = None,
) -> None:
    try:
        AccessPolicy().authorize(
            principal,
            action,
            resource_tenant_id=resource_tenant_id,
        )
    except AccessDeniedError as exc:
        raise HTTPException(
            status_code=403,
            detail=exc.decision.model_dump(mode="json"),
        ) from exc


def _load_authorized_task(
    task_id: str,
    principal: AccessPrincipal,
):
    state = CheckpointStore().load(task_id)
    _authorize(
        principal,
        "task.read",
        resource_tenant_id=state.principal.tenant_id,
    )
    return state


class TaskRequest(BaseModel):
    goal: str
    approval: Approval = Field(default_factory=Approval)


class CopilotMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, min_length=4, max_length=128)
    client_request_id: str | None = Field(default=None, min_length=4, max_length=128)


class PriceConfirmationRequest(BaseModel):
    action: Literal[
        "adopt_suggested_price",
        "keep_original_with_evidence",
        "market_analysis_only",
    ]
    selected_price: float | None = Field(default=None, gt=0)
    evidence: str | None = Field(default=None, max_length=500)
    expected_checkpoint_version: int = Field(ge=0)
    client_request_id: str = Field(min_length=4, max_length=128)


class NewConversationRequest(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=80)


class MemoryProposalRequest(BaseModel):
    scope: str = Field(default="global", min_length=1, max_length=120)
    memory_type: str = Field(default="merchant_preference", min_length=2, max_length=80)
    content: str = Field(min_length=2, max_length=1_000)
    sensitivity: Literal["public", "internal", "restricted"] = "internal"
    conflict_key: str | None = Field(default=None, max_length=120)
    valid_until: datetime | None = None


class MemoryConfirmationRequest(BaseModel):
    confirmed: bool = True


class CopilotApprovalRequest(BaseModel):
    expected_checkpoint_version: int | None = Field(default=None, ge=0)
    execution_plan_hash: str | None = Field(default=None, min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=500)


class CopilotBatchApprovalRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=5)
    expected_checkpoint_versions: dict[str, int] = Field(default_factory=dict)


class CopilotBatchRetryRequest(CopilotBatchApprovalRequest):
    reason: str | None = Field(default=None, max_length=500)


class CopilotBatchDispatchRequest(CopilotBatchApprovalRequest):
    operation: Literal["approve", "retry"] = "approve"
    client_request_id: str = Field(min_length=4, max_length=128)


class ResumeTaskRequest(BaseModel):
    approval: Approval = Field(default_factory=Approval)
    retry_node: str | None = None
    constraint_updates: dict[str, object] = Field(default_factory=dict)
    expected_checkpoint_version: int | None = Field(default=None, ge=0)
    requested_by: str | None = None
    reason: str | None = None


class LlmSmokeRequest(BaseModel):
    prompt: str
    json_schema: dict | None = None


class ExecutionRequest(BaseModel):
    plan: ExecutionPlan
    idempotency_key: str
    approval: Approval = Field(default_factory=Approval)


class TicketedExecutionRequest(BaseModel):
    ticket: str = Field(min_length=1, max_length=200)
    plan: ExecutionPlan


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tools")
def tools_catalog():
    return ToolRegistry().describe_tools()


@app.get("/api/a2a/capabilities")
def a2a_capabilities():
    return build_capability_catalog()


@app.get("/api/sql/schema")
def sql_schema_catalog():
    return get_market_sql_service().schema_catalog()


@app.get("/api/sql/audits")
def sql_audits(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return get_market_sql_service().audits(
        limit=limit, tenant_id=principal.tenant_id
    )


@app.get("/api/sandbox/status")
def sandbox_status():
    return get_market_sql_service().sandbox_status()


@app.get("/api/access/whoami")
def access_whoami(authorization: AuthorizationHeader = None):
    return _principal(authorization).model_dump(mode="json")


@app.get("/api/access/policy")
def access_policy():
    return AccessPolicy.catalog()


@app.get("/api/access/demo-identities")
def access_demo_identities():
    return identity_catalog()


@app.get("/api/access/audits")
def access_audits(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return [
        record
        for record in AccessAuditStore().read(limit=limit)
        if record.get("tenant_id") == principal.tenant_id
    ]


@app.get("/", response_class=HTMLResponse)
def root_page():
    return USER_HTML


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return USER_HTML


@app.get("/user", response_class=HTMLResponse)
def user_page():
    return USER_HTML


@app.get("/ops", response_class=HTMLResponse)
def ops_page():
    return DEMO_HTML


@app.get("/traces", response_class=HTMLResponse)
def traces_page():
    return TRACE_HTML


@app.get("/api/traces")
def list_traces(limit: int = Query(default=50, ge=1, le=200)):
    return TraceStore().list_runs(limit=limit)


@app.get("/api/traces/{run_id}")
def trace_detail(run_id: str):
    try:
        return TraceStore().get_run(run_id)
    except InvalidRunIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="trace not found") from exc


@app.get("/api/traces/{run_id}/summary")
def trace_summary(run_id: str):
    try:
        return TraceStore().get_summary(run_id)
    except InvalidRunIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="trace not found") from exc


@app.post("/tasks/run")
def run_task(request: TaskRequest, authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "task.run")
    if request.approval.approved:
        _authorize(principal, "task.approve")
    state = Supervisor().run(
        request.goal,
        approved=request.approval.approved,
        approved_by=request.approval.approver,
        approval_reason=request.approval.reason,
        principal=principal,
    )
    return state_response(state)


def require_linked_runtime() -> None:
    status = get_linked_runtime_status()
    if not status["ready"]:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "linked_runtime_unavailable",
                "issues": status["issues"],
            },
        )


@app.get("/linked/status")
def linked_status():
    return get_linked_runtime_status()


@app.post("/user/tasks/run")
def run_user_task(request: TaskRequest, authorization: AuthorizationHeader = None):
    require_linked_runtime()
    return run_task(request, authorization)


@app.post("/api/copilot/messages", response_model=CopilotResponse)
def copilot_message(
    request: CopilotMessageRequest,
    authorization: AuthorizationHeader = None,
):
    require_linked_runtime()
    principal = _principal(authorization)
    _authorize(principal, "task.run")
    try:
        return ConversationFacade().handle_message(
            request.message,
            principal=principal,
            conversation_id=request.conversation_id,
            client_request_id=request.client_request_id,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConversationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecoveryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConversationStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_public_copilot_error(exc)) from exc


@app.post(
    "/api/copilot/tasks/{task_id}/price-confirmation",
    response_model=CopilotResponse,
)
def confirm_market_price(
    task_id: str,
    request: PriceConfirmationRequest,
    authorization: AuthorizationHeader = None,
):
    """Append one explicit price choice to the task's existing conversation."""

    require_linked_runtime()
    principal = _principal(authorization)
    _authorize(principal, "task.run")
    try:
        state = _load_authorized_task(task_id, principal)
        if state.status != "waiting_for_input":
            raise HTTPException(
                status_code=409,
                detail="该任务当前不在等待价格确认状态，请刷新会话。",
            )
        if state.checkpoint_version != request.expected_checkpoint_version:
            raise HTTPException(
                status_code=409,
                detail="价格确认已过期，请刷新后使用最新结果。",
            )
        if not state.conversation_id:
            raise HTTPException(
                status_code=409,
                detail="该任务没有可恢复的会话记录。",
            )
        if request.action == "keep_original_with_evidence":
            evidence = (request.evidence or "").strip()
            if len(evidence) < 4:
                raise HTTPException(
                    status_code=422,
                    detail="保留原价需要填写可核验的差异化依据。",
                )
            message = f"保留原价，因为{evidence}"
        elif request.action == "market_analysis_only":
            message = "只看市场分析，不要继续上架"
        elif request.selected_price is not None:
            message = f"采用建议价格，调整售价为 {request.selected_price:g} 元"
        else:
            message = "采用建议价格"
        response = ConversationFacade().handle_message(
            message,
            principal=principal,
            conversation_id=state.conversation_id,
            client_request_id=request.client_request_id,
        )
        if response.task_id not in {None, task_id}:
            raise ConversationConflictError(
                "Price confirmation resumed an unexpected task"
            )
        return response
    except (CheckpointNotFoundError, InvalidTaskIdError) as exc:
        raise HTTPException(status_code=404, detail="任务不存在或已经过期。") from exc
    except StaleCheckpointError as exc:
        raise HTTPException(status_code=409, detail="价格确认已过期，请刷新后重试。") from exc
    except ConversationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecoveryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConversationStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_public_copilot_error(exc)) from exc


def _public_copilot_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError) and _is_listing_input_validation(exc):
        return (
            "输入中的价格、库存、成本或毛利率不在有效范围内。"
            "请使用大于0的售价、非负的成本和库存，以及0%到100%之间的最低毛利率。"
        )
    if isinstance(exc, ValidationError) and exc.title == "ModifyListingRequest":
        if any(error.get("loc") == ("changes",) for error in exc.errors(include_url=False)):
            return (
                "没有识别到可执行的商品修改项。请明确要修改的商品，"
                "以及售价、库存、优惠券或标题中的具体新值。"
            )
    return "本次请求没有完成。系统已记录技术信息，请稍后重试。"


def _is_listing_input_validation(exc: ValidationError) -> bool:
    """Do not mislabel an internal model/schema error as bad user numbers."""

    business_fields = {"cost", "target_price", "inventory", "min_margin_rate"}
    if exc.title != "CreateListingRequest":
        return False
    return any(
        error.get("loc") and str(error["loc"][0]) in business_fields
        for error in exc.errors(include_url=False)
    )


def _run_dispatched_message(
    request: CopilotMessageRequest,
    principal: AccessPrincipal,
    stream_id: str,
) -> dict[str, object]:
    events = CopilotEventStore()
    try:
        def publish(event: dict) -> None:
            events.append(principal.tenant_id, stream_id, **event)

        with bind_public_event_sink(publish):
            response = ConversationFacade().handle_message(
                request.message,
                principal=principal,
                conversation_id=request.conversation_id,
                client_request_id=request.client_request_id,
            )
        events.complete(principal.tenant_id, stream_id, response)
        return {
            "stream_id": stream_id,
            "conversation_id": response.conversation_id,
            "task_id": response.task_id,
            "outcome": response.outcome.value,
        }
    except Exception as exc:  # The stream must always reach one durable terminal event.
        events.fail(principal.tenant_id, stream_id, _public_copilot_error(exc))
        raise


def _workflow_job_handler(payload: dict[str, object]) -> dict[str, object]:
    request = CopilotMessageRequest.model_validate(payload["request"])
    principal = AccessPrincipal.model_validate(payload["principal"])
    return _run_dispatched_message(request, principal, str(payload["stream_id"]))


def _drain_workflow_queue() -> None:
    """Compatibility worker for the demo; the same queue can be drained externally."""

    runtime = DistributedRuntime()
    worker_id = f"api-worker-{uuid4().hex[:10]}"
    while runtime.run_once(
        worker_id=worker_id,
        pool="workflow",
        handlers={"copilot_turn": _workflow_job_handler},
    ) is not None:
        pass


def _execute_batch_job(request: BatchExecutionJobRequest) -> BatchExecutionReport:
    repository = ConversationRepository()
    batch = repository.get_batch_job(
        request.principal.tenant_id, request.batch_job_id
    )
    AccessPolicy().authorize(
        request.principal,
        "task.approve",
        resource_tenant_id=batch.tenant_id,
    )
    return BatchExecutionService(repository).execute(
        request.batch_job_id,
        principal=request.principal,
        item_ids=request.item_ids,
        expected_checkpoint_versions=request.expected_checkpoint_versions,
        retry_failed=request.operation == "retry",
    )


def _drain_batch_execution_queue() -> None:
    dispatcher = BatchExecutionDispatcher()
    worker_id = f"batch-browser-{uuid4().hex[:10]}"
    while dispatcher.run_once(
        worker_id=worker_id, executor=_execute_batch_job
    ) is not None:
        pass


@app.post("/api/copilot/messages/dispatch", response_model=CopilotDispatchResponse)
def dispatch_copilot_message(
    request: CopilotMessageRequest,
    authorization: AuthorizationHeader = None,
):
    """Start a turn and return immediately; progress is read from the SSE endpoint."""

    require_linked_runtime()
    principal = _principal(authorization)
    _authorize(principal, "task.run")
    repository = ConversationRepository()
    if request.conversation_id:
        repository.get_conversation(principal.tenant_id, request.conversation_id)
    else:
        request.conversation_id = repository.create_conversation(
            principal.tenant_id, title=request.message
        ).conversation_id
    if not request.client_request_id:
        request.client_request_id = f"request_{datetime.now().timestamp():.6f}".replace(".", "_")
    stream_id = CopilotEventStore().create(
        principal.tenant_id, request.conversation_id
    )
    runtime = DistributedRuntime()
    try:
        runtime.enqueue(
            tenant_id=principal.tenant_id,
            pool="workflow",
            job_type="copilot_turn",
            idempotency_key=(
                f"copilot:{request.conversation_id}:{request.client_request_id}"
            ),
            payload={
                "request": request.model_dump(mode="json"),
                "principal": principal.model_dump(mode="json"),
                "stream_id": stream_id,
            },
            # A conversation turn owns durable state and performs bounded retries
            # inside its agents. Re-running the whole turn with the same request ID
            # only collides with the in-progress reservation and duplicates events.
            max_attempts=1,
        )
    except (QueueBackpressureError, TenantRateLimitError) as exc:
        CopilotEventStore().fail(principal.tenant_id, stream_id, str(exc))
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except RuntimeIdempotencyConflict as exc:
        CopilotEventStore().fail(principal.tenant_id, stream_id, str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    Thread(
        target=_drain_workflow_queue,
        daemon=True,
        name=f"runtime-kick-{stream_id}",
    ).start()
    return CopilotDispatchResponse(
        stream_id=stream_id,
        conversation_id=request.conversation_id,
        events_url=f"/api/copilot/streams/{stream_id}/events",
    )


@app.get("/api/runtime/status")
def distributed_runtime_status(
    authorization: AuthorizationHeader = None,
):
    """Read-only queue, lease, Saga, Outbox and bulkhead status for operations."""

    principal = _principal(authorization)
    _authorize(principal, "task.read")
    return DistributedRuntime().snapshot(tenant_id=principal.tenant_id)


@app.get("/api/operations/readiness")
def operational_readiness(authorization: AuthorizationHeader = None):
    """Read-only v39 chaos, capacity, SLO and isolation evidence."""

    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return load_operational_report()


@app.get("/api/operations/{section}")
def operational_readiness_section(
    section: str,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    if section not in {"chaos", "capacity", "isolation", "slo"}:
        raise HTTPException(status_code=404, detail="Operational report section not found")
    report = load_operational_report()
    return report.get(section, {"status": "needs_validation"})


@app.get("/api/runtime/jobs/{job_id}")
def distributed_runtime_job(
    job_id: str,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    try:
        return DistributedRuntime().get_job(
            job_id, tenant_id=principal.tenant_id
        ).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runtime/sagas")
def distributed_runtime_sagas(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    return [
        item.model_dump(mode="json")
        for item in DistributedRuntime().sagas(
            tenant_id=principal.tenant_id, limit=limit
        )
    ]


@app.get(
    "/api/copilot/conversations/{conversation_id}/active-stream",
    response_model=ActiveStreamResponse | None,
)
def get_active_copilot_stream(
    conversation_id: str,
    authorization: AuthorizationHeader = None,
):
    """Return a currently running turn so a refreshed page can reconnect."""

    principal = _principal(authorization)
    _authorize(principal, "task.read")
    try:
        ConversationRepository().get_conversation(principal.tenant_id, conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    active = CopilotEventStore().active_stream(principal.tenant_id, conversation_id)
    return ActiveStreamResponse.model_validate(active) if active else None


@app.get("/api/copilot/streams/{stream_id}/events")
async def stream_copilot_events(
    stream_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    authorization: AuthorizationHeader = None,
):
    """Resume-safe SSE stream backed by SQLite rather than browser-local state."""

    principal = _principal(authorization)
    _authorize(principal, "task.read")
    try:
        cursor = max(after, int(last_event_id or 0))
        CopilotEventStore().status(principal.tenant_id, stream_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Event stream not found") from exc

    async def event_source():
        nonlocal cursor
        quiet_cycles = 0
        while not await request.is_disconnected():
            store = CopilotEventStore()
            events = store.events_after(principal.tenant_id, stream_id, cursor)
            if events:
                quiet_cycles = 0
                for event in events:
                    cursor = event.event_id
                    payload = event.model_dump(mode="json")
                    yield (
                        f"id: {event.event_id}\n"
                        f"event: {event.event_type}\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
            else:
                quiet_cycles += 1
            status = store.status(principal.tenant_id, stream_id)
            if status in {"completed", "failed"} and not events:
                break
            if quiet_cycles >= 40:
                quiet_cycles = 0
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.15)

    return StreamingResponse(
        event_source(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/copilot/conversations", response_model=ConversationRecord)
def create_copilot_conversation(
    request: NewConversationRequest,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.run")
    return ConversationRepository().create_conversation(
        principal.tenant_id,
        title=request.title,
    )


@app.get("/api/copilot/conversations", response_model=ConversationListResponse)
def list_copilot_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    query: Annotated[str | None, Query(max_length=120)] = None,
    product_id: Annotated[str | None, Query(max_length=128)] = None,
    approval_status: Literal["all", "pending"] = "all",
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    return ConversationListResponse(
        conversations=ConversationRepository().list_conversations(
            principal.tenant_id,
            limit=limit,
            query=query,
            product_id=product_id,
            approval_status=approval_status,
        )
    )


@app.get(
    "/api/copilot/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
)
def get_copilot_conversation(
    conversation_id: str,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    repository = ConversationRepository()
    try:
        detail = repository.get_detail(principal.tenant_id, conversation_id)
        latest_payload = repository.latest_response_payload(
            principal.tenant_id, conversation_id
        )
        latest_response = (
            CopilotResponse.model_validate(latest_payload)
            if latest_payload
            else None
        )
        return ConversationDetailResponse(
            detail=detail,
            latest_response=latest_response,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConversationStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/copilot/conversations/{conversation_id}/memory-summary",
    response_model=StructuredConversationSummary | None,
)
def get_conversation_memory_summary(
    conversation_id: str,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    repository = ConversationRepository()
    repository.get_conversation(principal.tenant_id, conversation_id)
    return ConversationMemoryService(repository).get_summary(
        principal.tenant_id, conversation_id
    )


@app.get("/api/copilot/conversations/{conversation_id}/context-status")
def get_conversation_context_status(
    conversation_id: str,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    repository = ConversationRepository()
    repository.get_conversation(principal.tenant_id, conversation_id)
    memory = ConversationMemoryService(repository)
    seed = memory.context_seed(principal.tenant_id, conversation_id)
    return {
        "protocol_version": "1.0",
        "conversation_id": conversation_id,
        "summary_trust": seed["summary_trust"],
        "context_budget": seed["context_budget"],
        "events": memory.list_context_events(principal.tenant_id, conversation_id),
    }


@app.post("/api/copilot/compile-preview")
def preview_compiled_request(
    request: CopilotMessageRequest,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.run")
    compiled = RequestCompiler().compile(request.message)
    compiled.route_plan = ConversationOrchestrator().plan(compiled)
    return compiled.model_dump(mode="json")


@app.get("/api/copilot/memories", response_model=list[MerchantMemory])
def list_merchant_memories(
    status: Literal["candidate", "active", "inactive", "conflicted"] | None = None,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    return LongTermMemory().list(principal.tenant_id, status=status)


@app.post("/api/copilot/memories", response_model=MerchantMemory)
def propose_merchant_memory(
    request: MemoryProposalRequest,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.run")
    return LongTermMemory().propose(
        principal.tenant_id,
        scope=request.scope,
        memory_type=request.memory_type,
        content=request.content,
        source="user_confirmable_api",
        sensitivity=request.sensitivity,
        conflict_key=request.conflict_key,
        valid_until=request.valid_until,
        metadata={"proposed_by": principal.subject_id},
    )


@app.post("/api/copilot/memories/{memory_id}/confirm", response_model=MerchantMemory)
def confirm_merchant_memory(
    memory_id: str,
    request: MemoryConfirmationRequest,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.approve")
    store = LongTermMemory()
    try:
        return (
            store.confirm(principal.tenant_id, memory_id, confirmed_by=principal.subject_id)
            if request.confirmed
            else store.deactivate(principal.tenant_id, memory_id)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/copilot/conversations/{conversation_id}/messages",
    response_model=CopilotResponse,
)
def send_copilot_conversation_message(
    conversation_id: str,
    request: CopilotMessageRequest,
    authorization: AuthorizationHeader = None,
):
    request.conversation_id = conversation_id
    return copilot_message(request, authorization)


@app.get("/api/copilot/tasks/{task_id}", response_model=CopilotResponse)
def copilot_task(task_id: str, authorization: AuthorizationHeader = None):
    try:
        state = _load_authorized_task(task_id, _principal(authorization))
        return ConversationFacade.build_response(state)
    except InvalidTaskIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CheckpointError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/copilot/products", response_model=list[ProductRecord])
def list_copilot_products(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    return ProductLedger().list_products(principal.tenant_id, limit=limit)


@app.get("/api/copilot/products/{product_id}", response_model=ProductDetail)
def get_copilot_product(
    product_id: str,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    try:
        return ProductLedger().detail(principal.tenant_id, product_id)
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/copilot/tasks/{task_id}/approve",
    response_model=CopilotResponse,
)
def approve_copilot_task(
    task_id: str,
    request: CopilotApprovalRequest,
    authorization: AuthorizationHeader = None,
):
    require_linked_runtime()
    try:
        principal = _principal(authorization)
        state = _load_authorized_task(task_id, principal)
        _authorize(
            principal,
            "task.approve",
            resource_tenant_id=state.principal.tenant_id,
        )
        current_response = ConversationFacade.build_response(state)
        if (
            request.execution_plan_hash is not None
            and request.execution_plan_hash != current_response.execution_plan_hash
        ):
            raise HTTPException(
                status_code=409,
                detail="方案已经变化，请刷新页面并重新确认最新方案。",
            )
        return ConversationFacade().approve(
            task_id,
            principal=principal,
            expected_checkpoint_version=request.expected_checkpoint_version,
            reason=request.reason,
        )
    except InvalidTaskIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (StaleCheckpointError, RecoveryConflictError, RecoveryNotAllowedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecoveryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/api/copilot/batches/{batch_job_id}/approve",
    response_model=BatchExecutionReport,
)
def approve_copilot_batch(
    batch_job_id: str,
    request: CopilotBatchApprovalRequest,
    authorization: AuthorizationHeader = None,
):
    require_linked_runtime()
    principal = _principal(authorization)
    repository = ConversationRepository()
    try:
        batch = repository.get_batch_job(principal.tenant_id, batch_job_id)
        _authorize(
            principal,
            "task.approve",
            resource_tenant_id=batch.tenant_id,
        )
        return BatchExecutionService(repository).execute(
            batch_job_id,
            principal=principal,
            item_ids=request.item_ids,
            expected_checkpoint_versions=request.expected_checkpoint_versions,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ConversationConflictError, StaleCheckpointError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/api/copilot/batches/{batch_job_id}/retry",
    response_model=BatchExecutionReport,
)
def retry_copilot_batch_items(
    batch_job_id: str,
    request: CopilotBatchRetryRequest,
    authorization: AuthorizationHeader = None,
):
    require_linked_runtime()
    principal = _principal(authorization)
    repository = ConversationRepository()
    try:
        batch = repository.get_batch_job(principal.tenant_id, batch_job_id)
        _authorize(principal, "task.approve", resource_tenant_id=batch.tenant_id)
        return BatchExecutionService(repository).execute(
            batch_job_id,
            principal=principal,
            item_ids=request.item_ids,
            expected_checkpoint_versions=request.expected_checkpoint_versions,
            retry_failed=True,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ConversationConflictError, StaleCheckpointError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/api/copilot/batches/{batch_job_id}/dispatch",
    response_model=BatchExecutionDispatch,
)
def dispatch_copilot_batch_execution(
    batch_job_id: str,
    request: CopilotBatchDispatchRequest,
    authorization: AuthorizationHeader = None,
):
    """Persist the selected write request before any browser action begins."""

    require_linked_runtime()
    principal = _principal(authorization)
    repository = ConversationRepository()
    try:
        batch = repository.get_batch_job(principal.tenant_id, batch_job_id)
        _authorize(principal, "task.approve", resource_tenant_id=batch.tenant_id)
        by_id = {
            item.item_id: item
            for item in repository.list_batch_items(principal.tenant_id, batch_job_id)
        }
        selected = [by_id.get(item_id) for item_id in request.item_ids]
        if any(item is None for item in selected):
            raise ConversationNotFoundError(
                "Selected batch items do not belong to this batch"
            )
        allowed_status = "awaiting_approval" if request.operation == "approve" else "needs_attention"
        invalid = [
            item.item_id for item in selected if item is not None and item.status != allowed_status
        ]
        if invalid:
            raise ConversationConflictError(
                f"Selected items are not in {allowed_status}: {', '.join(invalid)}"
            )
        dispatch = BatchExecutionDispatcher().enqueue(
            BatchExecutionJobRequest(
                batch_job_id=batch_job_id,
                operation=request.operation,
                item_ids=request.item_ids,
                expected_checkpoint_versions=request.expected_checkpoint_versions,
                execution_generations={
                    item.item_id: item.execution_attempts
                    for item in selected
                    if item is not None
                },
                principal=principal,
            ),
            client_request_id=request.client_request_id,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConversationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (QueueBackpressureError, TenantRateLimitError) as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except RuntimeIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    Thread(
        target=_drain_batch_execution_queue,
        daemon=True,
        name=f"batch-runtime-kick-{dispatch.runtime_job_id}",
    ).start()
    return dispatch


@app.get(
    "/api/copilot/batch-executions/{runtime_job_id}",
    response_model=BatchExecutionJobStatus,
)
def get_copilot_batch_execution(
    runtime_job_id: str,
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    try:
        return BatchExecutionDispatcher().status(
            runtime_job_id, tenant_id=principal.tenant_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Batch execution job not found") from exc


@app.get(
    "/api/copilot/batches/{batch_job_id}/executions/latest",
    response_model=BatchExecutionJobStatus | None,
)
def get_latest_copilot_batch_execution(
    batch_job_id: str,
    authorization: AuthorizationHeader = None,
):
    """Recover the newest durable execution receipt after a page reconnect."""

    principal = _principal(authorization)
    _authorize(principal, "task.read")
    try:
        ConversationRepository().get_batch_job(principal.tenant_id, batch_job_id)
        return BatchExecutionDispatcher().latest(
            batch_job_id, tenant_id=principal.tenant_id
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/tasks/checkpoints")
def list_task_checkpoints(
    limit: int = Query(default=50, ge=1, le=200),
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "task.read")
    store = CheckpointStore()
    visible = []
    for summary in store.list(limit=200):
        try:
            state = store.load(summary["task_id"])
        except CheckpointError:
            continue
        if state.principal.tenant_id == principal.tenant_id:
            visible.append(summary)
        if len(visible) >= limit:
            break
    return visible


@app.get("/tasks/{task_id}")
def get_task_checkpoint(
    task_id: str, authorization: AuthorizationHeader = None
):
    try:
        principal = _principal(authorization)
        return state_response(_load_authorized_task(task_id, principal))
    except InvalidTaskIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CheckpointError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/tasks/{task_id}/a2a")
def task_a2a_summary(task_id: str, authorization: AuthorizationHeader = None):
    try:
        state = _load_authorized_task(task_id, _principal(authorization))
        return build_task_collaboration_summary(state)
    except InvalidTaskIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CheckpointError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/tasks/{task_id}/security")
def task_security_summary(task_id: str, authorization: AuthorizationHeader = None):
    try:
        _load_authorized_task(task_id, _principal(authorization))
        return build_task_security_summary(task_id)
    except InvalidTaskIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (CheckpointError, SecurityLedgerError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/security/ledger/verify")
def verify_security_ledger():
    return SecurityLedger().verify_integrity()


@app.get("/api/release/readiness")
def release_readiness(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return build_release_readiness().model_dump(mode="json")


@app.get("/api/release/final")
def final_release_status(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return build_final_release_status().model_dump(mode="json")


@app.get("/api/release/v59")
def v59_release_status(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return build_v59_release_status().model_dump(mode="json")


@app.get("/api/tasks/{task_id}/linkage")
def task_linkage(task_id: str, authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    state = _load_authorized_task(task_id, principal)
    try:
        trace_chain = [TraceStore().get_run(state.run_id)]
    except TraceNotFoundError:
        trace_chain = []
    with tenant_scope(principal.tenant_id):
        seller_snapshot = get_seller_center_snapshot()
    return build_linkage_identity(
        state,
        trace_chain=trace_chain,
        seller_snapshot=seller_snapshot,
    )


@app.get("/api/release/protocols")
def release_protocols(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return build_protocol_manifest().model_dump(mode="json")


@app.get("/api/reliability/status")
def reliability_status(
    task_id: str | None = Query(default=None, min_length=4, max_length=128),
    authorization: AuthorizationHeader = None,
):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    prefix = f"{principal.tenant_id}:"
    circuits = [
        item.model_dump(mode="json")
        for item in GLOBAL_CIRCUITS.snapshots()
        if item.key.startswith(prefix)
    ]
    dead_letters = [
        item.model_dump(mode="json")
        for item in get_dead_letter_store().list(
            tenant_id=principal.tenant_id, task_id=task_id
        )
    ]
    return {
        "protocol_version": "1.0",
        "tenant_id": principal.tenant_id,
        "task_id": task_id,
        "circuits": circuits,
        "dead_letters": dead_letters,
        "needs_attention_count": sum(
            item["status"] == "needs_attention" for item in dead_letters
        ),
        "boundary": (
            "V37 circuit and DLQ state is process-local plus SQLite for one service instance; "
            "distributed coordination and fencing belong to V38."
        ),
    }


@app.get("/api/reliability/tool-contracts")
def reliability_tool_contracts(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return {"protocol_version": "2.0", "tools": ToolRegistry().describe_tools()}


@app.get("/api/release/threat-model")
def release_threat_model(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    return build_threat_model()


@app.get("/api/release/evidence")
def release_evidence(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    manifest = load_evidence_manifest()
    return {
        "integrity": current_evidence_status(),
        "manifest": manifest.model_dump(mode="json") if manifest else None,
    }


@app.post("/tasks/{task_id}/resume")
def resume_task(
    task_id: str,
    request: ResumeTaskRequest,
    authorization: AuthorizationHeader = None,
):
    try:
        principal = _principal(authorization)
        state = _load_authorized_task(task_id, principal)
        if request.approval.approved:
            _authorize(
                principal,
                "task.approve",
                resource_tenant_id=state.principal.tenant_id,
            )
        state = Supervisor().resume(
            task_id,
            approval=request.approval,
            retry_node=request.retry_node,
            constraint_updates=request.constraint_updates,
            expected_checkpoint_version=request.expected_checkpoint_version,
            requested_by=principal.subject_id,
            reason=request.reason,
        )
        return state_response(state)
    except (InvalidTaskIdError, RecoveryValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CheckpointNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (StaleCheckpointError, RecoveryConflictError, RecoveryNotAllowedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CheckpointError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/user/tasks/{task_id}/resume")
def resume_user_task(
    task_id: str,
    request: ResumeTaskRequest,
    authorization: AuthorizationHeader = None,
):
    require_linked_runtime()
    return resume_task(task_id, request, authorization)


@app.get("/seller-center/state")
def seller_center_state(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "seller.read")
    with tenant_scope(principal.tenant_id):
        return get_seller_center_snapshot()


@app.post("/seller-center/reset")
def seller_center_reset(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "seller.execute")
    with tenant_scope(principal.tenant_id):
        return reset_seller_center()


@app.post("/seller-center/execute")
def seller_center_execute(
    request: ExecutionRequest, authorization: AuthorizationHeader = None
):
    principal = _principal(authorization)
    _authorize(principal, "seller.execute")
    if not request.approval.approved:
        return {
            "status": "waiting_for_approval",
            "error": "human_approval_required",
            "execution_plan": request.plan.model_dump(mode="json"),
        }
    with tenant_scope(principal.tenant_id):
        return browser_execute(
            request.plan.model_dump(mode="json"),
            idempotency_key=request.idempotency_key,
        )


@app.post("/seller-center/verify")
def seller_center_verify(
    plan: ExecutionPlan, authorization: AuthorizationHeader = None
):
    principal = _principal(authorization)
    _authorize(principal, "seller.read")
    with tenant_scope(principal.tenant_id):
        return browser_verify(plan.model_dump(mode="json"))


@app.get("/api/execution/status")
def execution_isolation_status(authorization: AuthorizationHeader = None):
    principal = _principal(authorization)
    _authorize(principal, "evidence.read")
    with tenant_scope(principal.tenant_id):
        snapshot = get_seller_center_snapshot()
    return {
        "tenant_id": principal.tenant_id,
        "seller_center": {
            "storage": "process_local_partitioned_memory",
            "product_count": len(snapshot["products"]),
            "promotion_count": len(snapshot["promotions"]),
            "other_tenant_ids_exposed": False,
        },
        "browser_ticket": {
            "claims": ["tenant_id", "purpose", "product_id", "plan_fingerprint"],
            "one_time": True,
            "ttl_seconds": BROWSER_TICKET_TTL_SECONDS,
        },
        "idempotency": {
            "namespace": principal.tenant_id,
            "path": str(IDEMPOTENCY_DIR / principal.tenant_id / "records.json"),
        },
        "browser_artifacts": {
            "namespace": principal.tenant_id,
            "path": str(BROWSER_ARTIFACT_DIR / principal.tenant_id),
        },
        "boundary": (
            "Process-local demo partitions; no external Seller Center account, "
            "distributed lock, per-tenant encryption key, or infrastructure namespace."
        ),
    }


@app.get("/seller-center", response_class=HTMLResponse)
def seller_center_page():
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>EcomPilot Mock Seller Center</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 32px; color: #1f2937; }
    h1 { font-size: 24px; }
    pre { background: #f3f4f6; padding: 16px; border-radius: 8px; overflow: auto; }
    button { padding: 8px 12px; border: 1px solid #9ca3af; background: white; border-radius: 6px; cursor: pointer; }
  </style>
</head>
<body>
  <h1>EcomPilot Mock Seller Center</h1>
  <button onclick="loadState()">刷新状态</button>
  <button onclick="resetState()">重置</button>
  <pre id="state">loading...</pre>
  <script>
    async function loadState() {
      const res = await fetch('/seller-center/state');
      document.getElementById('state').textContent = JSON.stringify(await res.json(), null, 2);
    }
    async function resetState() {
      await fetch('/seller-center/reset', { method: 'POST' });
      await loadState();
    }
    loadState();
    setInterval(loadState, 1500);
  </script>
</body>
</html>
"""


@app.get("/seller-center/editor", response_class=HTMLResponse)
def seller_center_editor(request: Request):
    ticket = request.headers.get("X-EcomPilot-Browser-Ticket", "") or request.headers.get(
        "X-EcomPilot-Execution-Ticket", ""
    )
    try:
        BrowserTicketStore.inspect(ticket, purpose="execute")
        return seller_center_editor_html(ticket)
    except BrowserTicketError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/seller-center/ui/execute")
def seller_center_ui_execute(request: TicketedExecutionRequest):
    try:
        return execute_ticketed_plan(request.ticket, request.plan)
    except BrowserTicketError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/seller-center/products/{product_id}", response_class=HTMLResponse)
def seller_center_product(product_id: str, request: Request):
    ticket = request.headers.get("X-EcomPilot-Browser-Ticket", "") or request.headers.get(
        "X-EcomPilot-Execution-Ticket", ""
    )
    try:
        observed = observed_ticketed_product_state(ticket, product_id)
        return product_detail_html(product_id, observed)
    except BrowserTicketError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/llm/smoke")
def llm_smoke(request: LlmSmokeRequest):
    try:
        return ModelAdapter(provider=LLM_PROVIDER, model=LLM_MODEL).complete(
            request.prompt, json_schema=request.json_schema
        ).model_dump()
    except ModelProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/llm/status")
def llm_status():
    return get_llm_runtime_status()


@app.get("/browser/status")
def browser_status():
    return get_browser_runtime_status()


@app.post("/eval/regression")
def eval_regression():
    return run_eval(Path("data/eval/v9_regression_tasks.json"))


@app.post("/eval/recovery")
def eval_recovery():
    return run_recovery_eval(Path("data/eval/v13_recovery_cases.json"))


@app.post("/eval/llm")
def eval_llm():
    return run_llm_eval(Path("data/eval/v14_llm_tasks.json"))


@app.post("/eval/browser")
def eval_browser():
    return run_browser_eval(Path("data/eval/v15_browser_cases.json"))
