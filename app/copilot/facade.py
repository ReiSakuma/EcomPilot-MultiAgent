from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from app.access.models import AccessPrincipal
from app.agents.supervisor import Supervisor
from app.config import LLM_MODEL, LLM_PROVIDER
from app.copilot.batch import BatchRunReport
from app.copilot.schemas import (
    ActionStep,
    ActionSummary,
    CopilotResponse,
    ModelUsageSummary,
    PanelDescriptor,
    PriceConfirmationOption,
    PriceConfirmationPrompt,
    CopilotOutcome,
)
from app.copilot.intents import CompiledRequest, IntentName, RequestMode
from app.copilot.task_router import TaskRelationRouter
from app.conversations.models import TaskRouteDecision
from app.conversations.repository import (
    ConversationConflictError,
    ConversationRepository,
)
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.failures import TaskOutcome, failure_from_exception
from app.orchestration.state import TaskState
from app.observability.store import InvalidRunIdError, TraceNotFoundError, TraceStore
from app.presentation import build_task_presentation
from app.safety.approval import Approval
from app.model.adapter import ModelAdapter
from app.model.telemetry import completed_model_record, failed_model_record
from app.products.ledger import ProductLedger, ProductLedgerError
from app.products.models import ProductDetail
from app.memory.conversation import ConversationMemoryService
from app.tools.market_price_gate import (
    MarketPriceAssessment,
    parse_price_confirmation,
)


AGENT_LABELS = {
    "market_agent": "调研市场参考",
    "market_price_gate_agent": "检查目标售价与市场位置",
    "listing_agent": "生成商品页面方案",
    "strategy_agent": "制定定价与促销方案",
    "review_agent": "检查事实与执行风险",
    "browser_agent": "同步并核对模拟店铺",
    "analytics_agent": "查询商品销售表现",
}

PANEL_CONFIG = (
    ("market", "market", "市场参考", "market_agent"),
    ("listing", "listing", "商品页面方案", "listing_agent"),
    ("strategy", "strategy", "定价与促销", "strategy_agent"),
    ("review", "review", "风险检查", "review_agent"),
    ("execution", "browser", "店铺同步", "browser_agent"),
)

STUB_PROVIDERS = {"deterministic", "mock", "local", "rule"}


def _task_session_status_for_response(outcome: CopilotOutcome) -> str:
    return {
        CopilotOutcome.waiting_for_input: "waiting_for_input",
        CopilotOutcome.awaiting_approval: "awaiting_approval",
        CopilotOutcome.completed: "completed",
        CopilotOutcome.read_only_completed: "completed",
        CopilotOutcome.answered: "completed",
        CopilotOutcome.business_rejected: "business_rejected",
        CopilotOutcome.technical_failed: "technical_failed",
        CopilotOutcome.advisory: "completed",
        CopilotOutcome.out_of_scope: "completed",
    }.get(outcome, "running")


class ConversationFacade:
    """V33 conversation boundary with persistent, tenant-scoped memory."""

    def __init__(
        self,
        supervisor_factory: Callable[[], Supervisor] = Supervisor,
        repository: ConversationRepository | None = None,
    ) -> None:
        self._supervisor_factory = supervisor_factory
        self.repository = repository or ConversationRepository()
        self.memory_service = ConversationMemoryService(self.repository)

    def handle_message(
        self,
        message: str,
        *,
        principal: AccessPrincipal,
        conversation_id: str | None = None,
        client_request_id: str | None = None,
    ) -> CopilotResponse:
        from app.copilot.graph import V33ConversationGraph

        conversation = (
            self.repository.get_conversation(principal.tenant_id, conversation_id)
            if conversation_id
            else self.repository.create_conversation(principal.tenant_id, title=message)
        )
        reservation = self.repository.begin_turn(
            principal.tenant_id,
            conversation.conversation_id,
            client_request_id=client_request_id or f"req_{uuid4().hex[:16]}",
            message=message,
        )
        if not reservation.created:
            stored_response = self.repository.response_for_turn(
                principal.tenant_id, reservation.turn.turn_id
            )
            if stored_response:
                return CopilotResponse.model_validate(stored_response)
            if reservation.turn.task_id:
                state = CheckpointStore().load(reservation.turn.task_id)
                if state.principal.tenant_id != principal.tenant_id:
                    raise ConversationConflictError(
                        "Task tenant does not match request tenant"
                    )
                return self.build_response(state)
            raise ConversationConflictError("The same request is still being processed")
        try:
            graph = V33ConversationGraph(
                self._supervisor_factory,
                repository=self.repository,
            )
            pending = self.repository.get_pending_request(
                principal.tenant_id, conversation.conversation_id
            )
            sessions = self.repository.list_task_sessions(
                principal.tenant_id, conversation.conversation_id
            )
            route = TaskRelationRouter().route(
                message,
                sessions=sessions,
                active_task_session_id=conversation.active_task_session_id,
                pending=pending,
            )
            task_session_id = route.target_task_session_id
            if route.relation == "new_task":
                if pending and pending.task_session_id:
                    self.repository.set_task_pending_status(
                        principal.tenant_id, pending.task_session_id, "suspended"
                    )
                    self.repository.update_task_session(
                        principal.tenant_id,
                        pending.task_session_id,
                        status="suspended",
                    )
                session = self.repository.create_task_session(
                    principal.tenant_id,
                    conversation.conversation_id,
                    reservation.turn.turn_id,
                    intent="pending_classification",
                    title=message,
                )
                task_session_id = session.task_session_id
                route = TaskRouteDecision(
                    **{
                        **route.model_dump(),
                        "target_task_session_id": task_session_id,
                    }
                )
                pending = None
            elif task_session_id:
                if (
                    pending
                    and pending.task_session_id
                    and pending.task_session_id != task_session_id
                ):
                    self.repository.set_task_pending_status(
                        principal.tenant_id, pending.task_session_id, "suspended"
                    )
                    self.repository.update_task_session(
                        principal.tenant_id,
                        pending.task_session_id,
                        status="suspended",
                    )
                self.repository.link_turn_to_task(
                    principal.tenant_id,
                    reservation.turn.turn_id,
                    task_session_id,
                    relation={
                        "continue_task": "continued",
                        "recall_task": "recalled",
                        "switch_task": "switched",
                    }.get(route.relation, "continued"),
                )
                target_pending = self.repository.get_task_pending_request(
                    principal.tenant_id, task_session_id
                )
                if target_pending is not None:
                    pending = target_pending
            self.repository.record_task_route(
                principal.tenant_id,
                conversation.conversation_id,
                reservation.turn.turn_id,
                route,
            )
            if task_session_id:
                self.repository.set_active_task_session(
                    principal.tenant_id,
                    conversation.conversation_id,
                    task_session_id,
                )
            checkpoint_thread_id = (
                pending.checkpoint_thread_id
                if pending and pending.checkpoint_thread_id
                else self.repository.get_task_session(
                    principal.tenant_id, task_session_id
                ).checkpoint_thread_id
                if task_session_id
                else f"message_{reservation.turn.turn_id}"
            )
            pending_payload_override: dict[str, Any] | None = None
            price_confirmation = (
                dict(pending.compiled_payload.get("_market_price_confirmation") or {})
                if pending
                else {}
            )
            if pending and price_confirmation:
                response, compiled, consumed = self._resume_price_confirmation(
                    message,
                    principal=principal,
                    conversation_id=conversation.conversation_id,
                    turn_id=reservation.turn.turn_id,
                    pending_payload=dict(pending.compiled_payload),
                    confirmation=price_confirmation,
                )
                if consumed:
                    self.repository.clear_pending_request(
                        principal.tenant_id,
                        conversation.conversation_id,
                        task_session_id=pending.task_session_id,
                    )
                else:
                    pending_payload_override = dict(pending.compiled_payload)
            elif pending:
                # A clarification is single-use. Consume it before resuming so a
                # downstream failure cannot leave a stale checkpoint attached to
                # every later user message in this conversation.
                self.repository.clear_pending_request(
                    principal.tenant_id,
                    conversation.conversation_id,
                    task_session_id=pending.task_session_id,
                )
                response, _graph_steps, compiled = graph.resume(
                    message,
                    conversation_id=conversation.conversation_id,
                    turn_id=reservation.turn.turn_id,
                    thread_id=checkpoint_thread_id,
                )
            else:
                response, _graph_steps, compiled = graph.invoke(
                    message,
                    principal=principal,
                    conversation_id=conversation.conversation_id,
                    turn_id=reservation.turn.turn_id,
                    thread_id=checkpoint_thread_id,
                )
            response_payload = response.model_dump(mode="json")
            if response.outcome is CopilotOutcome.waiting_for_input:
                self.repository.save_pending_request(
                    principal.tenant_id,
                    conversation.conversation_id,
                    compiled_payload=(
                        pending_payload_override
                        or self._pending_payload(compiled, response.task_id)
                    ),
                    clarification_round=compiled.assessment.clarification_round,
                    last_question=response.assistant_message,
                    task_session_id=task_session_id,
                    checkpoint_thread_id=checkpoint_thread_id,
                )
                if task_session_id:
                    self.repository.update_task_session(
                        principal.tenant_id,
                        task_session_id,
                        status="waiting_for_input",
                        intent=compiled.decision.intent.value,
                    )
            else:
                self.repository.clear_pending_request(
                    principal.tenant_id,
                    conversation.conversation_id,
                    task_session_id=task_session_id,
                )
            if response.task_id and compiled.decision.intent in {
                IntentName.create_listing,
                IntentName.modify_listing,
                IntentName.market_research,
                IntentName.product_performance,
            }:
                state = CheckpointStore().load(response.task_id)
                self.repository.complete_turn(
                    state,
                    response.assistant_message,
                    response_payload=response_payload,
                )
                self.memory_service.capture_task_entities(state)
            else:
                self.repository.complete_message_turn(
                    principal.tenant_id,
                    conversation.conversation_id,
                    reservation.turn.turn_id,
                    intent=compiled.decision.intent.value,
                    assistant_message=response.assistant_message,
                    response_payload=response_payload,
                    task_id=response.task_id,
                    product_refs=response.entity_refs,
                )
                if task_session_id:
                    self.repository.update_task_session(
                        principal.tenant_id,
                        task_session_id,
                        status=_task_session_status_for_response(response.outcome),
                        intent=compiled.decision.intent.value,
                    )
            self.memory_service.refresh_summary(
                principal.tenant_id, conversation.conversation_id
            )
            return response
        except Exception as exc:
            self.repository.fail_turn(
                principal.tenant_id,
                reservation.turn.turn_id,
                type(exc).__name__,
            )
            raise

    def _resume_price_confirmation(
        self,
        message: str,
        *,
        principal: AccessPrincipal,
        conversation_id: str,
        turn_id: str,
        pending_payload: dict[str, Any],
        confirmation: dict[str, Any],
    ) -> tuple[CopilotResponse, CompiledRequest, bool]:
        compiled_payload = dict(pending_payload)
        compiled_payload.pop("_market_price_confirmation", None)
        compiled = CompiledRequest.model_validate(compiled_payload)
        assessment = MarketPriceAssessment.model_validate(confirmation["assessment"])
        parsed = parse_price_confirmation(message, assessment)
        if parsed.action == "unknown":
            state = CheckpointStore().load(str(confirmation["task_id"]))
            if state.principal.tenant_id != principal.tenant_id:
                raise ConversationConflictError(
                    "Price confirmation task does not belong to request tenant"
                )
            response = self.build_response(state, compiled=compiled)
            response.turn_id = turn_id
            response.task_id = None
            response.assistant_message = _price_confirmation_message(
                assessment,
                prefix="我还不能判断你选择了哪一种处理方式。",
            )
            response.action_summary.headline = "等待明确的价格处理选择。"
            return response, compiled, False

        updates: dict[str, Any] = {
            "pricing_confirmation_action": parsed.action,
            "price_confirmation_request_id": str(confirmation["request_id"]),
        }
        if parsed.selected_price is not None:
            updates["target_price"] = parsed.selected_price
        if parsed.evidence:
            updates["pricing_override_evidence"] = list(parsed.evidence)
        state = self._supervisor_factory().resume(
            str(confirmation["task_id"]),
            constraint_updates=updates,
            expected_checkpoint_version=int(confirmation["checkpoint_version"]),
            requested_by=principal.subject_id,
            reason=f"用户选择价格处理动作：{parsed.action}",
            turn_id=turn_id,
        )
        if state.principal.tenant_id != principal.tenant_id:
            raise ConversationConflictError(
                "Price confirmation task does not belong to request tenant"
            )
        return self.build_response(state, compiled=compiled), compiled, True

    @staticmethod
    def _pending_payload(
        compiled: CompiledRequest, task_id: str | None
    ) -> dict[str, Any]:
        payload = compiled.model_dump(mode="json")
        if not task_id:
            return payload
        state = CheckpointStore().load(task_id)
        if state.status != "waiting_for_input":
            return payload
        gate = state.agent_outputs.get("market_price_gate_agent")
        if not gate or gate.get("status") != "confirmation_required":
            return payload
        payload["_market_price_confirmation"] = {
            "protocol_version": "1.0",
            "request_id": f"price_confirm_{state.task_id}_{state.checkpoint_version}",
            "task_id": state.task_id,
            "run_id": state.run_id,
            "checkpoint_version": state.checkpoint_version,
            "assessment": gate,
        }
        return payload
    def approve(
        self,
        task_id: str,
        *,
        principal: AccessPrincipal,
        approver: str | None = None,
        reason: str | None = None,
        expected_checkpoint_version: int | None = None,
    ) -> CopilotResponse:
        state = self._supervisor_factory().resume(
            task_id,
            approval=Approval(
                approved=True,
                approver=approver or principal.subject_id,
                reason=reason or "用户在对话工作台确认执行方案",
            ),
            expected_checkpoint_version=expected_checkpoint_version,
            requested_by=principal.subject_id,
            reason=reason or "用户确认同步到模拟店铺",
        )
        browser = state.agent_outputs.get("browser_agent", {})
        product_detail = None
        if browser.get("verification", {}).get("verified"):
            try:
                product = ProductLedger(
                    self.repository.database_path
                ).record_successful_execution(state)
                state.entity_refs = sorted(set([*state.entity_refs, product.product_id]))
                CheckpointStore().save(state)
                product_detail = ProductLedger(
                    self.repository.database_path
                ).detail(state.principal.tenant_id, product.product_id)
            except ProductLedgerError as exc:
                state.degradations.append(
                    failure_from_exception(
                        exc,
                        stage="product_identity_index",
                        agent_name="conversation_facade",
                        trace_refs=(state.run_id,),
                    )
                )
        response = self.build_response(state, product_detail=product_detail)
        self.repository.update_task(
            state,
            assistant_message=response.assistant_message,
            response_payload=response.model_dump(mode="json"),
        )
        self.memory_service.capture_task_entities(state)
        if state.conversation_id:
            self.memory_service.refresh_summary(
                state.principal.tenant_id, state.conversation_id
            )
        return response

    @staticmethod
    def build_response(
        state: TaskState,
        *,
        compiled: CompiledRequest | None = None,
        product_detail: ProductDetail | None = None,
    ) -> CopilotResponse:
        presentation = build_task_presentation(state)
        if state.intent == "product_performance":
            analytics = dict(state.agent_outputs.get("analytics_agent", {}))
            completed = state.outcome is TaskOutcome.completed and bool(analytics)
            return CopilotResponse(
                conversation_id=state.conversation_id,
                turn_id=state.turn_id,
                thread_id=state.conversation_id,
                task_id=state.task_id,
                run_id=state.run_id,
                outcome=(
                    CopilotOutcome.read_only_completed
                    if completed
                    else CopilotOutcome.technical_failed
                ),
                intent=compiled.decision if compiled else None,
                assessment=compiled.assessment if compiled else None,
                route_plan=compiled.route_plan if compiled else None,
                data_scope=compiled.decision.data_scope if compiled else [],
                entity_refs=list(state.entity_refs),
                assistant_message=(
                    str(analytics.get("narrative")) + _synthetic_notice(analytics)
                    if completed
                    else "销售表现暂时无法查询。"
                    + (f"{state.failure.user_message}" if state.failure else "数据源当前没有可用记录。")
                ),
                understood_requirements=dict(state.constraints),
                action_summary=_action_summary(state, False),
                panels=[
                    PanelDescriptor(
                        panel_id="analytics",
                        title="销售表现",
                        status="completed" if completed else "failed",
                        summary=(
                            _analytics_summary(analytics)
                            if completed
                            else "销售指标尚不可用，未生成任何估算数字。"
                        ),
                        data=analytics,
                        source_agents=["analytics_agent"],
                        artifact_refs=[state.latest_artifacts["analytics_agent"]]
                        if state.latest_artifacts.get("analytics_agent")
                        else [],
                    )
                ],
                model_usage=_model_usage(state),
                approval_required=False,
                store_modified=False,
                failure=presentation.failure,
                links={
                    "operations": f"/ops?task_id={state.task_id}&pin=1",
                    "trace": f"/traces?run_id={state.run_id}&pin=1",
                    "seller_center": "/seller-center",
                },
            )
        if state.intent == "market_research":
            market = dict(state.agent_outputs.get("market_agent", {}))
            return CopilotResponse(
                conversation_id=state.conversation_id,
                turn_id=state.turn_id,
                thread_id=state.conversation_id,
                task_id=state.task_id,
                run_id=state.run_id,
                outcome=(
                    CopilotOutcome.read_only_completed
                    if state.outcome is TaskOutcome.completed
                    else CopilotOutcome.technical_failed
                ),
                intent=compiled.decision if compiled else None,
                assessment=compiled.assessment if compiled else None,
                route_plan=compiled.route_plan if compiled else None,
                data_scope=compiled.decision.data_scope if compiled else [],
                entity_refs=list(state.entity_refs),
                assistant_message=_market_message(state, market),
                understood_requirements=dict(state.constraints),
                action_summary=_action_summary(state, False),
                panels=[
                    _requirements_panel(state),
                    PanelDescriptor(
                        panel_id="market",
                        title="市场调研结果",
                        status="completed" if market else "failed",
                        summary=_panel_summary("market", "completed", market),
                        data=market,
                        source_agents=["market_agent"],
                        artifact_refs=[state.latest_artifacts["market_agent"]]
                        if state.latest_artifacts.get("market_agent")
                        else [],
                    ),
                ],
                model_usage=_model_usage(state),
                approval_required=False,
                store_modified=False,
                failure=presentation.failure,
                links={
                    "operations": f"/ops?task_id={state.task_id}&pin=1",
                    "trace": f"/traces?run_id={state.run_id}&pin=1",
                    "seller_center": "/seller-center",
                },
            )
        panels = [_requirements_panel(state)]
        panels.extend(_business_panels(state, presentation.outcome))
        if product_detail:
            panels.extend(_product_panels(product_detail))
        public_outcome: CopilotOutcome = {
            TaskOutcome.created: CopilotOutcome.created,
            TaskOutcome.running: CopilotOutcome.running,
            TaskOutcome.awaiting_approval: CopilotOutcome.awaiting_approval,
            TaskOutcome.waiting_for_input: CopilotOutcome.waiting_for_input,
            TaskOutcome.completed: CopilotOutcome.completed,
            TaskOutcome.business_rejected: CopilotOutcome.business_rejected,
            TaskOutcome.technical_failed: CopilotOutcome.technical_failed,
            # The public contract intentionally collapses reconciliation-required
            # execution states into a technical failure while preserving details.
            TaskOutcome.needs_attention: CopilotOutcome.technical_failed,
        }[presentation.outcome]
        if (
            presentation.outcome is TaskOutcome.completed
            and state.constraints.get("pricing_confirmation_action")
            == "market_analysis_only"
        ):
            public_outcome = CopilotOutcome.read_only_completed
        return CopilotResponse(
            conversation_id=state.conversation_id,
            turn_id=state.turn_id,
            thread_id=state.conversation_id,
            task_id=state.task_id,
            run_id=state.run_id,
            outcome=public_outcome,
            intent=compiled.decision if compiled else None,
            assessment=compiled.assessment if compiled else None,
            route_plan=compiled.route_plan if compiled else None,
            data_scope=compiled.decision.data_scope if compiled else [],
            entity_refs=list(state.entity_refs),
            assistant_message=_assistant_message(state, presentation.outcome),
            understood_requirements=dict(state.constraints),
            action_summary=_action_summary(state, presentation.store_modified),
            panels=panels,
            model_usage=_model_usage(state),
            price_confirmation=_price_confirmation_prompt(state),
            approval_required=presentation.outcome is TaskOutcome.awaiting_approval,
            store_modified=presentation.store_modified,
            failure=presentation.failure,
            links={
                "operations": f"/ops?task_id={state.task_id}&pin=1",
                "trace": f"/traces?run_id={state.run_id}&pin=1",
                "seller_center": "/seller-center",
            },
        )

    @staticmethod
    def build_product_response(
        compiled: CompiledRequest,
        *,
        detail: dict[str, Any] | None,
        resolution: dict[str, Any],
        conversation_id: str,
        turn_id: str,
    ) -> CopilotResponse:
        if not detail:
            return CopilotResponse(
                conversation_id=conversation_id,
                turn_id=turn_id,
                thread_id=conversation_id,
                outcome=CopilotOutcome.answered,
                intent=compiled.decision,
                assessment=compiled.assessment,
                route_plan=compiled.route_plan,
                data_scope=compiled.decision.data_scope,
                assistant_message="我没有在当前商户可见的商品中找到对应记录。你可以提供商品 ID、SKU 或更完整的商品名称。",
                understood_requirements=dict(compiled.structured_request),
                action_summary=_empty_action_summary("商品身份查询已完成，未修改店铺。"),
                panels=[],
                model_usage=_model_usage_records(compiled.compiler_model_records),
                approval_required=False,
                store_modified=False,
                links={"seller_center": "/seller-center"},
            )
        product_detail = ProductDetail.model_validate(detail)
        product = product_detail.product
        event_names = "、".join(event.summary for event in product_detail.timeline[-3:])
        return CopilotResponse(
            conversation_id=conversation_id,
            turn_id=turn_id,
            thread_id=conversation_id,
            task_id=product.source_task_id,
            outcome=CopilotOutcome.answered,
            intent=compiled.decision,
            assessment=compiled.assessment,
            route_plan=compiled.route_plan,
            data_scope=compiled.decision.data_scope,
            entity_refs=[product.product_id],
            assistant_message=(
                f"已找到“{product.title}”。商品 ID 为 {product.product_id}，"
                f"SKU 为 {product.sku or '未设置'}，当前状态为 {product.status}。"
                + (f"最近记录：{event_names}。" if event_names else "")
            ),
            understood_requirements=dict(compiled.structured_request),
            action_summary=_empty_action_summary("已读取商品账本、任务关联和店铺快照。"),
            panels=_product_panels(product_detail),
            model_usage=_model_usage_records(compiled.compiler_model_records),
            approval_required=False,
            store_modified=False,
            links={
                "operations": f"/ops?task_id={product.source_task_id}&pin=1",
                "seller_center": "/seller-center",
            },
        )

    @staticmethod
    def build_product_performance_not_found(
        compiled: CompiledRequest,
        *,
        resolution: dict[str, Any],
        conversation_id: str,
        turn_id: str,
    ) -> CopilotResponse:
        return CopilotResponse(
            conversation_id=conversation_id,
            turn_id=turn_id,
            thread_id=conversation_id,
            outcome=CopilotOutcome.answered,
            intent=compiled.decision,
            assessment=compiled.assessment,
            route_plan=compiled.route_plan,
            data_scope=compiled.decision.data_scope,
            assistant_message=(
                "我没有在当前商户可见的商品中找到可查询的记录。"
                "请提供商品 ID、SKU，或先在本会话中完成一次商品上架。"
            ),
            understood_requirements=dict(compiled.structured_request),
            action_summary=_empty_action_summary("商品身份解析已完成，未访问其他租户数据。"),
            panels=[],
            model_usage=_model_usage_records(compiled.compiler_model_records),
            approval_required=False,
            store_modified=False,
            links={"seller_center": "/seller-center"},
        )

    @staticmethod
    def build_memory_candidate_response(
        compiled: CompiledRequest,
        *,
        candidate: dict[str, Any],
        conversation_id: str,
        turn_id: str,
    ) -> CopilotResponse:
        memory_id = str(candidate.get("memory_id") or "")
        content = str(candidate.get("content") or compiled.structured_request.get("content") or "")
        return CopilotResponse(
            conversation_id=conversation_id,
            turn_id=turn_id,
            thread_id=conversation_id,
            outcome=CopilotOutcome.answered,
            intent=compiled.decision,
            assessment=compiled.assessment,
            route_plan=compiled.route_plan,
            data_scope=compiled.decision.data_scope,
            memory_refs=[memory_id] if memory_id else [],
            assistant_message=(
                f"我已把“{content}”保存为候选偏好。确认前它不会影响后续 Agent；"
                "请在记忆确认接口或运维记忆面板确认后再启用。"
            ),
            understood_requirements=dict(compiled.structured_request),
            action_summary=_empty_action_summary("已保存候选偏好，尚未激活。"),
            panels=[PanelDescriptor(
                panel_id="memory",
                title="长期偏好候选",
                status="waiting",
                summary="等待用户或有审批权限的运营人员确认。",
                data=candidate,
                source_agents=[],
                artifact_refs=[],
            )],
            model_usage=_model_usage_records(compiled.compiler_model_records),
            approval_required=False,
            store_modified=False,
            links={
                "memory_confirm": f"/api/copilot/memories/{memory_id}/confirm"
                if memory_id else "/api/copilot/memories"
            },
        )

    @staticmethod
    def build_non_task_response(
        compiled: CompiledRequest,
        *,
        conversation_id: str,
        turn_id: str,
        assistant_message: str | None = None,
    ) -> CopilotResponse:
        mode = compiled.assessment.mode
        if assistant_message is None and compiled.batch_plan is not None:
            if compiled.batch_plan.status == "ready":
                assistant_message = (
                    f"已确认包含 {len(compiled.batch_plan.items)} 个商品的批次，"
                    "并为每个商品建立了独立任务。v46 会在确认后分别生成方案，"
                    "并隔离每个子任务的失败。"
                )
            elif compiled.batch_plan.status == "blocked":
                assistant_message = "该多商品批次已取消或被安全策略阻止，没有启动子任务执行。"
        if assistant_message is None:
            if mode is RequestMode.advisory:
                issue_messages = [
                    issue.message for issue in compiled.assessment.preflight_issues
                ]
                assistant_message = (
                    "前置安全与业务可行性检查没有通过，我不会启动市场调研、方案生成或店铺写入。"
                    + ("".join(issue_messages) if issue_messages else "")
                    + "请提供真实且唯一的成本、售价、库存和已确认商品事实后重新提交。"
                )
            elif compiled.decision.intent is IntentName.out_of_scope:
                assistant_message = (
                    "这个请求不在当前版本可安全执行的范围内。我不会调用工具或修改店铺。"
                    f"{compiled.decision.rationale}"
                )
            else:
                assistant_message = _general_chat_answer(compiled)
        outcome = {
            RequestMode.clarify: CopilotOutcome.waiting_for_input,
            RequestMode.advisory: CopilotOutcome.advisory,
            RequestMode.general_chat: CopilotOutcome.answered,
            RequestMode.out_of_scope: CopilotOutcome.out_of_scope,
        }.get(mode, CopilotOutcome.answered)
        return CopilotResponse(
            conversation_id=conversation_id,
            turn_id=turn_id,
            thread_id=conversation_id,
            outcome=outcome,
            intent=compiled.decision,
            assessment=compiled.assessment,
            route_plan=compiled.route_plan,
            data_scope=compiled.decision.data_scope,
            assistant_message=assistant_message,
            understood_requirements=dict(compiled.structured_request),
            action_summary=_empty_action_summary(
                "等待用户补充信息。"
                if outcome is CopilotOutcome.waiting_for_input
                else "本次没有调用业务工具。"
            ),
            panels=[],
            model_usage=_model_usage_records(compiled.compiler_model_records),
            approval_required=False,
            store_modified=False,
            links={"seller_center": "/seller-center"},
        )

    @staticmethod
    def build_batch_response(
        compiled: CompiledRequest,
        report: BatchRunReport,
        *,
        conversation_id: str,
        turn_id: str,
    ) -> CopilotResponse:
        successful = [
            item for item in report.items
            if item.status in {"awaiting_approval", "completed"}
        ]
        failed = [
            item for item in report.items if item.status in {"failed", "skipped"}
        ]
        steps = [
            ActionStep(
                step_id=item.item_id,
                title=f"生成{item.label}方案",
                status=(
                    "completed"
                    if item.status in {"awaiting_approval", "completed"}
                    else "failed"
                    if item.status == "failed"
                    else "skipped"
                ),
                detail=(
                    f"独立任务 {item.task_id} 已生成并等待单项确认。"
                    if item.task_id
                    else item.error_message or "该商品没有启动执行。"
                ),
                agent_name="batch_orchestrator",
                tool_names=[],
                trace_refs=[item.run_id] if item.run_id else [],
            )
            for item in report.items
        ]
        records = [
            *compiled.compiler_model_records,
            *(record for item in report.items for record in item.model_records),
        ]
        item_data = [item.model_dump(mode="json") for item in report.items]
        if failed and successful:
            message = (
                f"批次中的 {len(successful)} 个商品已分别生成方案，"
                f"{len(failed)} 个商品未完成。失败没有影响其他商品；"
                "每个成功商品都保留了独立任务和审批状态。"
            )
        elif successful:
            message = (
                f"已为 {len(successful)} 个商品分别生成独立方案。"
                "它们尚未写入店铺，需要按商品逐项确认执行。"
            )
        else:
            message = "本批次没有商品成功生成方案，请根据各商品的失败原因调整后重试。"
        return CopilotResponse(
            conversation_id=conversation_id,
            turn_id=turn_id,
            thread_id=conversation_id,
            outcome=(
                CopilotOutcome.awaiting_approval
                if successful
                else CopilotOutcome.technical_failed
            ),
            intent=compiled.decision,
            assessment=compiled.assessment,
            route_plan=compiled.route_plan,
            data_scope=compiled.decision.data_scope,
            assistant_message=message,
            understood_requirements=dict(compiled.structured_request),
            action_summary=ActionSummary(
                headline=f"批次已处理：成功 {len(successful)}，未完成 {len(failed)}。",
                steps=steps,
                completed_step_count=len(successful),
                total_step_count=len(steps),
                tool_call_count=sum(item.tool_call_count for item in report.items),
                trace_event_count=0,
                execution_performed=False,
            ),
            panels=[
                PanelDescriptor(
                    panel_id="requirements",
                    title="批量商品任务",
                    status=(
                        "completed" if not failed else "blocked" if not successful else "ready"
                    ),
                    summary=message,
                    data={
                        "batch_job_id": report.batch_job_id,
                        "batch_status": report.status,
                        "items": item_data,
                    },
                    source_agents=["batch_orchestrator"],
                )
            ],
            model_usage=_model_usage_records(records),
            # The aggregate is not itself an approval token. V46's batch action
            # explicitly selects child plans and consumes each child's checkpoint.
            approval_required=False,
            store_modified=False,
            links={
                "operations": f"/ops?batch_job_id={report.batch_job_id}",
                "seller_center": "/seller-center",
            },
        )

    @staticmethod
    def build_status_response(
        compiled: CompiledRequest,
        *,
        task_state: TaskState | None,
        conversation_id: str,
        turn_id: str,
    ) -> CopilotResponse:
        if task_state is None:
            message = "当前会话还没有可查询的任务。"
            return CopilotResponse(
                conversation_id=conversation_id,
                turn_id=turn_id,
                thread_id=conversation_id,
                outcome=CopilotOutcome.answered,
                intent=compiled.decision,
                assessment=compiled.assessment,
                route_plan=compiled.route_plan,
                data_scope=compiled.decision.data_scope,
                assistant_message=message,
                action_summary=_empty_action_summary("未找到历史任务。"),
                panels=[],
                model_usage=_model_usage_records(compiled.compiler_model_records),
                approval_required=False,
                store_modified=False,
            )
        presentation = build_task_presentation(task_state)
        response = ConversationFacade.build_response(task_state)
        response.turn_id = turn_id
        response.intent = compiled.decision
        response.assessment = compiled.assessment
        response.route_plan = compiled.route_plan
        response.data_scope = compiled.decision.data_scope
        response.assistant_message = (
            f"任务 {task_state.task_id} 当前状态为 {presentation.outcome.value}。"
            f"已完成 {response.action_summary.completed_step_count}/"
            f"{response.action_summary.total_step_count} 个环节。"
        )
        response.model_usage = _model_usage_records(compiled.compiler_model_records)
        response.approval_required = False
        return response


def _requirements_panel(state: TaskState) -> PanelDescriptor:
    return PanelDescriptor(
        panel_id="requirements",
        title="系统理解的需求",
        status="ready",
        summary="这些字段由当前消息提取，可在对话工作台修正后重新生成。",
        data=dict(state.constraints),
    )


def _product_panels(detail: ProductDetail) -> list[PanelDescriptor]:
    product = detail.product
    seller = dict(product.seller_snapshot)
    return [
        PanelDescriptor(
            panel_id="product",
            title="商品档案",
            status="completed",
            summary=f"{product.title} · {product.status}",
            data={
                "product_id": product.product_id,
                "sku": product.sku,
                "title": product.title,
                "category": product.category,
                "status": product.status,
                "source_task_id": product.source_task_id,
                "price": seller.get("price"),
                "stock": seller.get("stock"),
                "seller_snapshot": seller,
            },
            source_agents=["product_ledger"],
            artifact_refs=sorted(
                {
                    ref
                    for link in detail.task_links
                    for ref in link.artifact_refs
                    if ref
                }
            ),
        ),
        PanelDescriptor(
            panel_id="timeline",
            title="商品时间线",
            status="completed",
            summary=f"记录了 {len(detail.timeline)} 个可追溯事件。",
            data={
                "events": [event.model_dump(mode="json") for event in detail.timeline],
                "task_links": [link.model_dump(mode="json") for link in detail.task_links],
            },
            source_agents=["product_ledger"],
            artifact_refs=[],
        ),
    ]


def _business_panels(
    state: TaskState, outcome: TaskOutcome
) -> list[PanelDescriptor]:
    panels: list[PanelDescriptor] = []
    for panel_id, node_id, title, agent_name in PANEL_CONFIG:
        output = dict(state.agent_outputs.get(agent_name, {}))
        if panel_id == "market":
            assessment = state.agent_outputs.get("market_price_gate_agent")
            if assessment:
                output["price_assessment"] = dict(assessment)
        node = state.nodes.get(node_id)
        status = _panel_status(
            panel_id, node.status.value if node else "pending", output, outcome
        )
        artifact_ref = state.latest_artifacts.get(agent_name)
        panels.append(
            PanelDescriptor(
                panel_id=panel_id,
                title=title,
                status=status,
                summary=_panel_summary(panel_id, status, output),
                data=output,
                source_agents=[agent_name],
                artifact_refs=[artifact_ref] if artifact_ref else [],
            )
        )
    return panels


def _panel_status(
    panel_id: str,
    node_status: str,
    output: dict[str, Any],
    outcome: TaskOutcome,
) -> str:
    if node_status == "failed":
        return "failed"
    if node_status == "skipped" and outcome is TaskOutcome.awaiting_approval:
        return "waiting"
    if output:
        if (
            panel_id == "review"
            and outcome is TaskOutcome.business_rejected
            and node_status == "completed"
        ):
            return "blocked"
        return "completed"
    if node_status in {"pending", "skipped"}:
        return "not_run"
    return node_status


def _panel_summary(panel_id: str, status: str, output: dict[str, Any]) -> str:
    if status == "not_run":
        return "尚未执行。"
    if status == "waiting":
        return "等待用户确认后执行。"
    if status == "failed":
        return "该环节没有生成可用结果。"
    if panel_id == "market":
        band = output.get("price_band")
        return f"参考价格区间 {band[0]} 至 {band[1]} 元。" if band else "市场参考已生成。"
    if panel_id == "listing":
        return output.get("title", "商品页面方案已生成。")
    if panel_id == "strategy":
        margin = output.get("margin", {}).get("margin_rate")
        return f"定价方案已生成，预计毛利率 {margin:.2%}。" if margin is not None else "定价方案已生成。"
    if panel_id == "review":
        return "方案已通过执行前检查。" if output.get("approved_for_execution") else "方案需要调整。"
    verification = output.get("verification", {})
    return "店铺同步和回读验证均已完成。" if verification.get("verified") else "店铺同步结果待确认。"


def _action_summary(state: TaskState, store_modified: bool) -> ActionSummary:
    try:
        trace_events = TraceStore().get_run(state.run_id)["events"]
    except (InvalidRunIdError, TraceNotFoundError):
        trace_events = []
    steps: list[ActionStep] = []
    for node_id, node in state.nodes.items():
        tools = sorted(
            {
                str(record.get("tool_name"))
                for record in state.tool_records
                if record.get("agent_name") == node.agent_name and record.get("tool_name")
            }
        )
        artifact_ref = state.latest_artifacts.get(node.agent_name)
        trace_refs = [
            str(event["event_id"])
            for event in trace_events
            if event.get("component_name") == node.agent_name and event.get("event_id")
        ]
        if node_id == "market" and state.agent_outputs.get("market_agent", {}).get(
            "market_statistics"
        ):
            steps.append(
                ActionStep(
                    step_id="market_data_cleaning",
                    title="清洗市场数据",
                    status=(
                        "completed"
                        if node.status.value == "completed"
                        else node.status.value
                    ),
                    detail=(
                        "市场样本清洗已完成。"
                        if node.status.value == "completed"
                        else "等待清洗市场样本。"
                    ),
                    agent_name="market_agent",
                    tool_names=tools,
                    artifact_refs=[artifact_ref] if artifact_ref else [],
                    trace_refs=trace_refs,
                )
            )
        steps.append(
            ActionStep(
                step_id=node_id,
                title=AGENT_LABELS.get(node.agent_name, node.agent_name),
                status=node.status.value,
                detail=_step_detail(node.status.value, node.agent_name),
                agent_name=node.agent_name,
                tool_names=tools,
                artifact_refs=[artifact_ref] if artifact_ref else [],
                trace_refs=trace_refs,
            )
        )
    completed = sum(step.status == "completed" for step in steps)
    return ActionSummary(
        headline=(
            "方案已写入模拟店铺并完成核对。"
            if store_modified
            else f"已完成 {completed}/{len(steps)} 个业务环节。"
        ),
        steps=steps,
        completed_step_count=completed,
        total_step_count=len(steps),
        tool_call_count=len(state.tool_records),
        trace_event_count=len(trace_events),
        execution_performed=store_modified,
    )


def _price_confirmation_prompt(state: TaskState) -> PriceConfirmationPrompt | None:
    if state.status != "waiting_for_input":
        return None
    payload = state.agent_outputs.get("market_price_gate_agent")
    if not payload or payload.get("status") != "confirmation_required":
        return None
    assessment = MarketPriceAssessment.model_validate(payload)
    market = state.agent_outputs.get("market_agent", {})
    layers = market.get("market_layers", {})
    adjacent = layers.get("adjacent_tier", {})
    adjacent_distribution = adjacent.get("price_distribution", {})
    adjacent_band = _distribution_band(adjacent_distribution)
    core = layers.get("core_comparable", {})
    full = layers.get("full_valid_market", {})
    suggested_price = None
    if assessment.suggested_price_range:
        low, high = assessment.suggested_price_range
        suggested_price = float(round((low + high) / 2))
    return PriceConfirmationPrompt(
        task_id=state.task_id,
        run_id=state.run_id,
        checkpoint_version=state.checkpoint_version,
        position=assessment.position,
        target_price=assessment.target_price,
        core_reference_price=assessment.core_reference_price,
        deviation_rate=assessment.deviation_rate,
        acceptance_band=assessment.acceptance_band,
        suggested_price_range=assessment.suggested_price_range,
        core_price_band=assessment.core_price_band,
        adjacent_price_band=adjacent_band,
        full_market_band=assessment.full_market_band,
        evidence_quality=assessment.evidence_quality,
        core_sample_count=assessment.core_sample_count,
        adjacent_sample_count=int(adjacent.get("sample_count") or 0),
        full_market_sample_count=int(full.get("sample_count") or 0),
        excluded_sample_count=assessment.excluded_sample_count,
        options=[
            PriceConfirmationOption(
                action="adopt_suggested_price",
                label="采用建议价格",
                description="使用建议区间中的价格继续生成商品和促销方案。",
                suggested_price=suggested_price,
            ),
            PriceConfirmationOption(
                action="keep_original_with_evidence",
                label="保留原价",
                description="说明可核验的材质、品牌或功能依据后继续。",
                requires_evidence=True,
            ),
            PriceConfirmationOption(
                action="market_analysis_only",
                label="只看市场分析",
                description="结束本次上新流程，不生成方案，也不修改店铺。",
            ),
        ],
    )


def _distribution_band(distribution: dict[str, Any]) -> tuple[float, float] | None:
    low = distribution.get("minimum")
    high = distribution.get("maximum")
    if low is None or high is None:
        return None
    return float(low), float(high)


def _step_detail(status: str, agent_name: str) -> str:
    label = AGENT_LABELS.get(agent_name, agent_name)
    return {
        "completed": f"{label}已完成。",
        "failed": f"{label}未完成。",
        "skipped": f"{label}尚未执行。",
        "running": f"正在{label}。",
        "pending": f"等待{label}。",
    }.get(status, label)


def _model_usage(state: TaskState) -> ModelUsageSummary:
    return _model_usage_records(list(state.model_records))


def _model_usage_records(records: list[dict[str, Any]]) -> ModelUsageSummary:
    real = [
        record
        for record in records
        if str(record.get("provider", "")).lower() not in STUB_PROVIDERS
    ]
    stub = [record for record in records if record not in real]
    mode = "real_model" if real else "test_stub" if stub else "no_model_call"
    return ModelUsageSummary(
        configured_provider=LLM_PROVIDER,
        configured_model=LLM_MODEL,
        recorded_call_count=len(records),
        actual_call_count=len(real),
        stub_call_count=len(stub),
        mode=mode,
        providers_used=sorted(
            {str(record.get("provider")) for record in records if record.get("provider")}
        ),
    )


def _empty_action_summary(headline: str) -> ActionSummary:
    return ActionSummary(
        headline=headline,
        steps=[],
        completed_step_count=0,
        total_step_count=0,
        tool_call_count=0,
        trace_event_count=0,
        execution_performed=False,
    )


def _market_message(state: TaskState, market: dict[str, Any]) -> str:
    if state.failure:
        return f"市场调研没有完成：{state.failure.user_message}"
    band = market.get("price_band")
    sample = market.get("sample_size", {})
    if band:
        return (
            f"我只读取了市场样本，没有创建商品或修改店铺。参考价格区间为 "
            f"{band[0]} 至 {band[1]} 元，中位价格 {market.get('median_price', '-')} 元，"
            f"共参考 {sample.get('competitors', 0)} 个商品样本。"
        )
    return "市场只读调研已完成，本次没有创建商品或修改店铺。"


def _analytics_summary(analytics: dict[str, Any]) -> str:
    metrics = analytics.get("sales", {}).get("metrics", {})
    period = analytics.get("period", {}).get("label", "所选期间")
    return (
        f"{period}售出 {metrics.get('units_sold', 0)} 件，"
        f"销售额 {float(metrics.get('revenue', 0)):.2f} 元。"
    )


def _synthetic_notice(analytics: dict[str, Any]) -> str:
    updated_at = analytics.get("source_updated_at", "-")
    if analytics.get("source_type") != "synthetic_demo":
        return f" 数据更新时间：{updated_at}。"
    return (
        " 当前展示的是可重复生成的模拟演示数据（synthetic_demo），"
        f"不是电商平台真实订单；数据更新时间：{updated_at}。"
    )


def _general_chat_answer(compiled: CompiledRequest) -> str:
    question = str(compiled.structured_request.get("question") or "")
    if LLM_PROVIDER.lower() in STUB_PROVIDERS:
        return (
            "这个问题不需要调用电商工具。当前处于确定性测试模式，因此我只确认："
            f"已将它识别为普通问答，未读取市场数据，也未修改店铺。原问题：{question}"
        )
    adapter = ModelAdapter(LLM_PROVIDER, LLM_MODEL)
    try:
        response = adapter.complete(
            "你是简洁、诚实的通用问答助手。本次不得调用任何电商工具，也不得声称已查询外部数据。"
            f"\n用户问题：{question}",
            max_output_tokens=700,
        )
        compiled.compiler_model_records.append(
            completed_model_record(
                response,
                agent_name="general_chat",
                purpose="isolated_general_answer",
            )
        )
        return response.text.strip()
    except Exception as exc:
        compiled.compiler_model_records.append(
            failed_model_record(
                adapter,
                exc,
                agent_name="general_chat",
                purpose="isolated_general_answer",
            )
        )
        return "普通问答模型暂时不可用；本次没有调用业务工具，也没有修改店铺。"


def _assistant_message(state: TaskState, outcome: TaskOutcome) -> str:
    listing = state.agent_outputs.get("listing_agent", {})
    strategy = state.agent_outputs.get("strategy_agent", {})
    margin = strategy.get("margin", {})
    if outcome is TaskOutcome.waiting_for_input:
        assessment_payload = state.agent_outputs.get("market_price_gate_agent", {})
        if assessment_payload and assessment_payload.get("status") == "confirmation_required":
            return _price_confirmation_message(
                MarketPriceAssessment.model_validate(assessment_payload)
            )
        return "继续处理前还需要你确认一项关键信息；当前没有修改店铺。"
    if outcome is TaskOutcome.awaiting_approval:
        if state.intent == "modify_listing":
            labels = {
                "target_price": "售价",
                "inventory": "库存",
                "coupon": "优惠金额",
                "title": "商品标题",
            }
            changes = "、".join(
                f"{labels.get(str(item['field']), item['field'])}改为 {item['new_value']}"
                for item in state.constraints.get("change_plan", [])
            )
            return (
                f"我已找到原商品并生成字段级变更计划：{changes}。"
                "未列出的店铺字段会保持原值；当前尚未修改店铺，请核对后确认执行。"
            )
        price = strategy.get("price", state.constraints.get("target_price", "-"))
        rate = margin.get("margin_rate")
        margin_text = f"，预计毛利率 {rate:.2%}" if rate is not None else ""
        title = listing.get("title", "商品页面方案")
        plan_message = (
            f"我已经完成市场参考、商品文案、定价和风险检查。建议售价 {price} 元"
            f"{margin_text}，商品标题为“{title}”。方案尚未修改店铺，请核对后确认执行。"
        )
        market_analysis = _listing_market_analysis(state)
        return (
            f"{market_analysis}\n\n{plan_message}"
            if market_analysis
            else plan_message
        )
    if outcome is TaskOutcome.completed:
        if state.constraints.get("pricing_confirmation_action") == "market_analysis_only":
            return (
                "已按你的选择保留市场调研结果。本次没有生成商品或促销方案，"
                "也没有修改模拟店铺。"
            )
        if state.intent == "modify_listing":
            return "字段级变更已同步到模拟店铺，未授权字段保持原值，并已完成回读核对。"
        return "方案已经同步到模拟店铺，并完成商品、价格、库存和促销信息的回读核对。"
    if outcome is TaskOutcome.business_rejected:
        message = state.failure.user_message if state.failure else "当前方案没有通过业务规则检查。"
        blocking_findings = [
            item
            for item in state.agent_outputs.get("review_agent", {}).get(
                "review_findings", []
            )
            if item.get("blocking")
        ]
        if blocking_findings and all(
            item.get("claim_origin") == "agent_generated"
            and item.get("user_action_required") is False
            for item in blocking_findings
        ):
            return (
                "我没有执行店铺写入。系统生成的内容仍未通过内部审核；"
                "你的原始业务条件无需调整，请重新生成方案。"
            )
        return f"我没有执行店铺写入。{message}请调整条件后重新生成。"
    if outcome is TaskOutcome.technical_failed:
        message = state.failure.user_message if state.failure else "系统没有完成当前任务。"
        if state.failure and state.failure.code == "model_network_unavailable":
            return message
        return f"本次处理遇到技术问题，店铺没有被修改。{message}"
    if outcome is TaskOutcome.needs_attention:
        message = (
            state.failure.user_message
            if state.failure
            else "店铺同步结果暂时无法确认，系统已停止重复写入。"
        )
        return (
            "本次店铺同步需要核对，系统没有自动重复执行。"
            f"{message}请在技术执行证据中确认状态后再重试。"
        )
    return "我已经收到请求，正在生成方案。"


def _listing_market_analysis(state: TaskState) -> str:
    """Render verified Market Agent evidence before asking for a write approval."""

    market = state.agent_outputs.get("market_agent", {})
    gate = state.agent_outputs.get("market_price_gate_agent", {})
    if not market:
        return ""
    target = gate.get("target_price", state.constraints.get("target_price"))
    reference = gate.get("core_reference_price", market.get("median_price"))
    band = gate.get("core_price_band") or market.get("price_band")
    sample_count = int(
        gate.get("core_sample_count")
        or market.get("sample_size", {}).get("competitors")
        or 0
    )
    excluded = int(
        gate.get("excluded_sample_count")
        or market.get("sample_size", {}).get("excluded_competitors")
        or 0
    )
    parts = ["市场价格分析："]
    if band and reference is not None:
        parts.append(
            f"核心可比商品价格区间为 {float(band[0]):g} 至 {float(band[1]):g} 元，"
            f"参考价为 {float(reference):g} 元。"
        )
    position = str(gate.get("position") or "")
    position_text = {
        "within_market": "位于当前市场接受区间内",
        "above_market": "高于当前市场接受区间",
        "below_market": "低于当前市场接受区间",
        "cost_market_conflict": "与成本及最低毛利要求存在冲突",
    }.get(position)
    if target is not None and position_text:
        deviation = gate.get("deviation_rate")
        deviation_text = (
            f"，相对参考价偏差 {float(deviation):+.2%}"
            if deviation is not None
            else ""
        )
        parts.append(
            f"你的目标售价 {float(target):g} 元{position_text}{deviation_text}。"
        )
    evidence_quality = str(gate.get("evidence_quality") or "")
    quality_text = {
        "high": "较高",
        "medium": "中等",
        "low": "较低",
    }.get(evidence_quality, evidence_quality)
    if sample_count or excluded:
        parts.append(
            f"本次使用 {sample_count} 个核心可比样本"
            + (f"，并排除 {excluded} 个异常样本" if excluded else "")
            + (f"，证据质量{quality_text}" if quality_text else "")
            + "。"
        )
    insight = str(
        market.get("sql_research", {}).get("insight_summary")
        if isinstance(market.get("sql_research"), dict)
        else ""
    ).strip()
    if insight:
        parts.append(f"Market Agent 的补充判断：{insight}")
    elif evidence_quality == "low":
        parts.append("当前核心样本较少，这一结论适合作为上新参考，不应视为确定的全市场结论。")
    return "".join(parts)


def _price_confirmation_message(
    assessment: MarketPriceAssessment,
    *,
    prefix: str = "",
) -> str:
    reference = (
        f"{assessment.core_reference_price:.2f} 元"
        if assessment.core_reference_price is not None
        else "暂不可用"
    )
    band = (
        f"{assessment.acceptance_band[0]:.2f} 至 "
        f"{assessment.acceptance_band[1]:.2f} 元"
        if assessment.acceptance_band
        else "暂不可用"
    )
    suggested = (
        f"{assessment.suggested_price_range[0]:.2f} 至 "
        f"{assessment.suggested_price_range[1]:.2f} 元"
        if assessment.suggested_price_range
        else "当前没有同时满足市场位置和毛利要求的区间"
    )
    position_text = {
        "above_market": "高于市场接受区间",
        "below_market": "低于市场接受区间",
        "cost_market_conflict": "与成本及最低毛利要求冲突",
    }.get(assessment.position, "需要再次确认")
    lead = f"{prefix} " if prefix else ""
    return (
        f"{lead}你的目标售价 {assessment.target_price:.2f} 元{position_text}。"
        f"核心可比商品参考价为 {reference}，当前接受区间为 {band}；"
        f"结合最低毛利要求，建议售价区间为 {suggested}。"
        "继续前请选择：回复“采用建议价格”，或回复“保留原价，因为……”并说明可核验依据，"
        "也可以回复“只看市场分析”。确认前不会生成后续方案或修改店铺。"
    )
