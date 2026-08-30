from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from app.access.models import AccessPrincipal
from app.agents.supervisor import Supervisor
from app.config import THREAD_CHECKPOINT_DATABASE_PATH
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.batch import BatchRunReport, BoundedBatchOrchestrator
from app.copilot.intents import CompiledRequest, IntentName, RequestMode
from app.copilot.multi_intent import MultiIntentExecutor
from app.copilot.routing import ConversationOrchestrator
from app.copilot.schemas import ActionSummary, CopilotOutcome, CopilotResponse, ModelUsageSummary
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.state import TaskState
from app.observability.recorder import TraceRecorder
from app.observability.schemas import TraceEventType
from app.products.ledger import ProductLedger
from app.products.resolver import EntityResolver
from app.memory.conversation import ConversationMemoryService
from app.memory.long_term import LongTermMemory


class V30GraphState(TypedDict, total=False):
    message: str
    principal: dict
    conversation_id: str
    turn_id: str
    active_turn_id: str
    compiled_request: dict
    task_state: dict
    entity_resolution: dict
    product_detail: dict
    response: dict
    memory_candidate: dict
    multi_intent_report: dict
    batch_run_report: dict
    graph_steps: list[str]


V29GraphState = V30GraphState


class ThreadCheckpointerRegistry:
    """One locked SqliteSaver per database path for the synchronous demo runtime."""

    _lock = RLock()
    _savers: dict[Path, SqliteSaver] = {}

    @classmethod
    def get(cls, path: Path | None = None) -> SqliteSaver:
        database_path = (path or THREAD_CHECKPOINT_DATABASE_PATH).resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with cls._lock:
            saver = cls._savers.get(database_path)
            if saver is None:
                connection = sqlite3.connect(database_path, check_same_thread=False)
                saver = SqliteSaver(connection)
                saver.setup()
                cls._savers[database_path] = saver
            return saver


class V29ConversationGraph:
    """Intent-routed outer graph with resumable clarification interrupts."""

    def __init__(
        self,
        supervisor_factory: Callable[[], Supervisor] = Supervisor,
        *,
        compiler: RequestCompiler | None = None,
        repository: ConversationRepository | None = None,
        checkpointer: SqliteSaver | None = None,
    ) -> None:
        self._supervisor_factory = supervisor_factory
        self._compiler = compiler or RequestCompiler()
        self._orchestrator = ConversationOrchestrator()
        self._repository = repository or ConversationRepository()
        self._memory_service = ConversationMemoryService(self._repository)
        self._graph = self._build_graph(checkpointer or ThreadCheckpointerRegistry.get())

    def invoke(
        self,
        message: str,
        *,
        principal: AccessPrincipal,
        conversation_id: str,
        turn_id: str,
        thread_id: str | None = None,
    ) -> tuple[CopilotResponse, list[str], CompiledRequest]:
        checkpoint_thread_id = thread_id or conversation_id
        result = self._graph.invoke(
            {
                "message": message,
                "principal": principal.model_dump(mode="json"),
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "active_turn_id": turn_id,
                "graph_steps": [],
            },
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        return self._project_result(
            result, conversation_id, turn_id, checkpoint_thread_id
        )

    def resume(
        self,
        message: str,
        *,
        conversation_id: str,
        turn_id: str,
        thread_id: str | None = None,
    ) -> tuple[CopilotResponse, list[str], CompiledRequest]:
        checkpoint_thread_id = thread_id or conversation_id
        result = self._graph.invoke(
            Command(resume={"message": message, "turn_id": turn_id}),
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        return self._project_result(
            result, conversation_id, turn_id, checkpoint_thread_id
        )

    def _project_result(
        self,
        result: dict[str, Any],
        conversation_id: str,
        turn_id: str,
        checkpoint_thread_id: str,
    ) -> tuple[CopilotResponse, list[str], CompiledRequest]:
        if result.get("__interrupt__"):
            payload = result["__interrupt__"][0].value
            compiled = CompiledRequest.model_validate(payload["compiled_request"])
            from app.copilot.facade import ConversationFacade

            response = ConversationFacade.build_non_task_response(
                compiled,
                conversation_id=conversation_id,
                turn_id=turn_id,
                assistant_message=str(payload["question"]),
            )
            response.thread_id = checkpoint_thread_id
            return response, list(result.get("graph_steps", [])), compiled
        compiled = CompiledRequest.model_validate(result["compiled_request"])
        response = CopilotResponse.model_validate(result["response"])
        response.thread_id = checkpoint_thread_id
        return (
            response,
            list(result.get("graph_steps", [])),
            compiled,
        )

    def _build_graph(self, checkpointer: SqliteSaver):
        builder = StateGraph(V30GraphState)
        builder.add_node("receive", self._receive)
        builder.add_node("compile_request", self._compile_request)
        builder.add_node("preflight_gate", self._preflight_gate)
        builder.add_node("listing_workflow", self._listing_workflow)
        builder.add_node("batch_listing_workflow", self._batch_listing_workflow)
        builder.add_node("modify_listing_workflow", self._modify_listing_workflow)
        builder.add_node("market_read_only", self._market_read_only)
        builder.add_node("multi_read_only", self._multi_read_only)
        builder.add_node("task_status", self._task_status)
        builder.add_node("product_detail", self._product_detail)
        builder.add_node("product_performance", self._product_performance)
        builder.add_node("general_chat", self._general_chat)
        builder.add_node("memory_candidate", self._memory_candidate)
        builder.add_node("out_of_scope", self._out_of_scope)
        builder.add_node("advisory", self._advisory)
        builder.add_node("answer", self._answer)
        builder.add_edge(START, "receive")
        builder.add_edge("receive", "compile_request")
        builder.add_edge("compile_request", "preflight_gate")
        builder.add_conditional_edges(
            "preflight_gate",
            self._route,
            {
                "listing_workflow": "listing_workflow",
                "batch_listing_workflow": "batch_listing_workflow",
                "modify_listing_workflow": "modify_listing_workflow",
                "market_read_only": "market_read_only",
                "multi_read_only": "multi_read_only",
                "task_status": "task_status",
                "product_detail": "product_detail",
                "product_performance": "product_performance",
                "general_chat": "general_chat",
                "memory_candidate": "memory_candidate",
                "out_of_scope": "out_of_scope",
                "advisory": "advisory",
            },
        )
        for node in (
            "listing_workflow",
            "batch_listing_workflow",
            "modify_listing_workflow",
            "market_read_only",
            "task_status",
            "product_detail",
            "product_performance",
            "general_chat",
            "memory_candidate",
            "out_of_scope",
            "advisory",
        ):
            builder.add_edge(node, "answer")
        builder.add_edge("multi_read_only", END)
        builder.add_edge("answer", END)
        return builder.compile(checkpointer=checkpointer)

    @staticmethod
    def _receive(state: V29GraphState) -> dict:
        message = state["message"].strip()
        if not message:
            raise ValueError("message must not be blank")
        return {"message": message, "graph_steps": ["receive"]}

    def _compile_request(self, state: V29GraphState) -> dict:
        compiled = self._compiler.compile(state["message"])
        compiled = self._materialize_batch_plan(
            compiled,
            state["principal"]["tenant_id"],
            state["conversation_id"],
            state["turn_id"],
        )
        active_turn_id = state["turn_id"]
        while compiled.assessment.mode is RequestMode.clarify:
            reply = interrupt(
                {
                    "kind": "clarification",
                    "question": compiled.assessment.clarification_question,
                    "compiled_request": compiled.model_dump(mode="json"),
                    "clarification_round": compiled.assessment.clarification_round,
                }
            )
            active_turn_id = str(reply["turn_id"])
            compiled = self._compiler.compile(
                str(reply["message"]),
                existing=compiled,
                clarification_round=compiled.assessment.clarification_round + 1,
            )
            compiled = self._materialize_batch_plan(
                compiled,
                state["principal"]["tenant_id"],
                state["conversation_id"],
                state["turn_id"],
            )
        compiled.route_plan = self._orchestrator.plan(compiled)
        return {
            "compiled_request": compiled.model_dump(mode="json"),
            "active_turn_id": active_turn_id,
            "graph_steps": [*state.get("graph_steps", []), "compile_request"],
        }

    def _materialize_batch_plan(
        self,
        compiled: CompiledRequest,
        tenant_id: str,
        conversation_id: str,
        turn_id: str,
    ) -> CompiledRequest:
        if compiled.batch_plan is None:
            return compiled
        batch, records = self._repository.materialize_batch_plan(
            tenant_id,
            conversation_id,
            turn_id,
            operation="create_listing",
            items=[
                {
                    "item_id": item.item_id,
                    "label": item.label,
                    "structured_request": item.structured_request,
                    "status": (
                        "ready"
                        if item.assessment.mode is RequestMode.execute
                        else "waiting_for_input"
                    ),
                }
                for item in compiled.batch_plan.items
            ],
        )
        by_item = {item.item_id: item for item in records}
        compiled.batch_plan.batch_job_id = batch.batch_job_id
        for item in compiled.batch_plan.items:
            item.task_session_id = by_item[item.item_id].task_session_id
        return compiled

    @staticmethod
    def _preflight_gate(state: V29GraphState) -> dict:
        """Make the write-admission boundary explicit before any specialist runs."""

        compiled = CompiledRequest.model_validate(state["compiled_request"])
        if (
            compiled.decision.intent is IntentName.create_listing
            and compiled.assessment.preflight_status != "passed"
        ):
            raise RuntimeError("blocked listing request reached specialist routing")
        return {
            "graph_steps": [*state.get("graph_steps", []), "preflight_gate"]
        }

    @staticmethod
    def _route(state: V29GraphState) -> str:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        if compiled.assessment.mode is RequestMode.advisory:
            return "advisory"
        if (
            compiled.batch_plan is not None
            and compiled.batch_plan.status == "ready"
            and compiled.assessment.mode is RequestMode.execute
        ):
            return "batch_listing_workflow"
        if (
            len(compiled.intent_units) > 1
            and compiled.route_plan is not None
            and not compiled.route_plan.clarification_required
            and all(unit.mode == "read_only" for unit in compiled.intent_units)
        ):
            return "multi_read_only"
        return {
            IntentName.create_listing: "listing_workflow",
            IntentName.modify_listing: "modify_listing_workflow",
            IntentName.market_research: "market_read_only",
            IntentName.task_status: "task_status",
            IntentName.product_detail: "product_detail",
            IntentName.product_performance: "product_performance",
            IntentName.general_chat: "general_chat",
            IntentName.remember_preference: "memory_candidate",
            IntentName.out_of_scope: "out_of_scope",
        }[compiled.decision.intent]

    def _listing_workflow(self, state: V29GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        principal = AccessPrincipal.model_validate(state["principal"])
        task_state = self._supervisor_factory().run(
            _listing_goal(compiled.structured_request),
            approved=False,
            principal=principal,
            conversation_id=state["conversation_id"],
            turn_id=state["active_turn_id"],
            intent="create_listing",
            context_seed=self._memory_service.context_seed(
                principal.tenant_id, state["conversation_id"]
            ),
        )
        _attach_compiler_records(task_state, compiled)
        _attach_route_plan(task_state, compiled)
        return {
            "task_state": task_state.model_dump(mode="json"),
            "graph_steps": [*state.get("graph_steps", []), "listing_workflow"],
        }

    def _batch_listing_workflow(self, state: V30GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        if compiled.batch_plan is None or not compiled.batch_plan.batch_job_id:
            raise RuntimeError("A materialized batch plan is required")
        principal = AccessPrincipal.model_validate(state["principal"])
        specs = {item.item_id: item for item in compiled.batch_plan.items}
        context_seed = self._memory_service.context_seed(
            principal.tenant_id, state["conversation_id"]
        )

        def run_item(record) -> TaskState:
            spec = specs[record.item_id]
            task_state = self._supervisor_factory().run(
                _listing_goal(spec.structured_request),
                approved=False,
                principal=principal,
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
                intent="create_listing",
                context_seed=context_seed,
            )
            _attach_compiler_records(task_state, compiled)
            _attach_route_plan(task_state, compiled)
            return task_state

        report = BoundedBatchOrchestrator(self._repository, max_workers=2).run(
            principal.tenant_id,
            compiled.batch_plan.batch_job_id,
            run_item,
        )
        return {
            "batch_run_report": report.model_dump(mode="json"),
            "graph_steps": [
                *state.get("graph_steps", []),
                "batch_listing_workflow",
            ],
        }

    def _modify_listing_workflow(self, state: V30GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        principal = AccessPrincipal.model_validate(state["principal"])
        ledger = ProductLedger(self._repository.database_path)
        resolver = EntityResolver(ledger, self._repository)
        resolution = resolver.resolve(
            principal.tenant_id,
            str(compiled.structured_request.get("query") or state["message"]),
            conversation_id=state["conversation_id"],
        )
        active_turn_id = state["active_turn_id"]
        clarification_round = 0
        while resolution.status == "ambiguous" and clarification_round < 3:
            choices = [
                f"{index + 1}. {item.title}（{item.sku or item.product_id}）"
                for index, item in enumerate(resolution.candidates)
            ]
            clarification = compiled.model_copy(deep=True)
            clarification.decision.intent = IntentName.clarify
            clarification.decision.original_intent = IntentName.modify_listing
            clarification.assessment.mode = RequestMode.clarify
            clarification.assessment.clarification_round = clarification_round
            clarification.assessment.clarification_question = (
                "我找到了多个商品。要修改哪一个？请回复序号、SKU 或商品 ID：\n"
                + "\n".join(choices)
            )
            reply = interrupt(
                {
                    "kind": "entity_resolution",
                    "question": clarification.assessment.clarification_question,
                    "compiled_request": clarification.model_dump(mode="json"),
                    "candidates": [item.model_dump(mode="json") for item in resolution.candidates],
                }
            )
            active_turn_id = str(reply["turn_id"])
            answer = str(reply["message"]).strip()
            selected = _candidate_selection(answer, resolution.candidates)
            resolution = (
                resolver.resolve(principal.tenant_id, selected, conversation_id=state["conversation_id"])
                if selected
                else resolver.resolve(principal.tenant_id, answer, conversation_id=None)
            )
            clarification_round += 1

        task_state: TaskState | None = None
        if resolution.status == "resolved" and resolution.product_id:
            detail = ledger.detail(principal.tenant_id, resolution.product_id)
            current = dict(detail.product.seller_snapshot)
            try:
                source_state = CheckpointStore().load(detail.product.source_task_id)
                source_constraints = dict(source_state.constraints)
            except Exception:
                source_constraints = {}
            payload = {
                "category": detail.product.category,
                "cost": source_constraints.get("cost"),
                "target_price": current.get("price"),
                "inventory": current.get("stock"),
                "min_margin_rate": source_constraints.get("min_margin_rate", 0.25),
                "target_audience": source_constraints.get("target_audience"),
                "confirmed_features": source_constraints.get("confirmed_features") or current.get("bullets") or [],
                "confirmed_product_form": source_constraints.get("confirmed_product_form"),
                "operation_goal": "按用户明确字段修改已有商品，其余店铺字段保持不变",
            }
            for change in compiled.structured_request["changes"]:
                if change["field"] == "target_price":
                    payload["target_price"] = change["new_value"]
                elif change["field"] == "inventory":
                    payload["inventory"] = change["new_value"]
            if payload["cost"] is None:
                payload["cost"] = 0
            task_state = self._supervisor_factory().run(
                _listing_goal(payload),
                approved=False,
                principal=principal,
                conversation_id=state["conversation_id"],
                turn_id=active_turn_id,
                intent="modify_listing",
                entity_refs=[resolution.product_id],
                constraint_overrides={
                    "product_id": resolution.product_id,
                    "current_snapshot": current,
                    "change_plan": list(compiled.structured_request["changes"]),
                },
                context_seed=self._memory_service.context_seed(
                    principal.tenant_id, state["conversation_id"]
                ),
            )
            self._repository.set_active_product(
                principal.tenant_id, state["conversation_id"], resolution.product_id
            )
            _attach_compiler_records(task_state, compiled)
            _attach_route_plan(task_state, compiled)
        return {
            "compiled_request": compiled.model_dump(mode="json"),
            "active_turn_id": active_turn_id,
            "entity_resolution": resolution.model_dump(mode="json"),
            "task_state": task_state.model_dump(mode="json") if task_state else {},
            "graph_steps": [*state.get("graph_steps", []), "modify_listing_workflow"],
        }

    def _market_read_only(self, state: V29GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        principal = AccessPrincipal.model_validate(state["principal"])
        request = compiled.structured_request
        task_state = self._supervisor_factory().run_market_research(
            state["message"],
            principal=principal,
            conversation_id=state["conversation_id"],
            turn_id=state["active_turn_id"],
            constraints={
                key: value
                for key, value in {
                    "category": request.get("category"),
                    "target_audience": request.get("target_audience"),
                    "time_range_days": request.get("time_range_days"),
                    "research_topics": request.get("topics"),
                }.items()
                if value is not None
            },
            context_seed=self._memory_service.context_seed(
                principal.tenant_id, state["conversation_id"]
            ),
        )
        _attach_compiler_records(task_state, compiled)
        _attach_route_plan(task_state, compiled)
        return {
            "task_state": task_state.model_dump(mode="json"),
            "graph_steps": [*state.get("graph_steps", []), "market_read_only"],
        }

    def _multi_read_only(self, state: V30GraphState) -> dict:
        """Execute independent read units concurrently and return one user-facing response."""
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        if compiled.route_plan is None:
            raise ValueError("multi-intent route plan is required")
        responses: dict[str, CopilotResponse] = {}

        def execute(unit, _dependencies):
            probe = self._compiler._compile_single(unit.text)  # validated unit-local request
            probe.intent_units = [unit]
            probe.route_plan = self._orchestrator.plan(probe)
            local_state = dict(state)
            local_state["compiled_request"] = probe.model_dump(mode="json")
            local_state["graph_steps"] = []
            branch = {
                IntentName.market_research: self._market_read_only,
                IntentName.task_status: self._task_status,
                IntentName.product_detail: self._product_detail,
                IntentName.product_performance: self._product_performance,
            }.get(unit.intent)
            if branch is None:
                raise ValueError(f"Unsupported parallel read intent: {unit.intent.value}")
            output = branch(local_state)
            answered = self._answer({**local_state, **output})
            response = CopilotResponse.model_validate(answered["response"])
            responses[unit.intent_id] = response
            return {
                "outcome": response.outcome.value,
                "artifact_refs": [
                    ref for panel in response.panels for ref in panel.artifact_refs
                ],
            }

        report = MultiIntentExecutor().execute(compiled.route_plan, execute)
        ordered = [
            responses[unit.intent_id]
            for unit in compiled.intent_units
            if unit.intent_id in responses
        ]
        if not ordered:
            raise RuntimeError("No multi-intent read result was produced")
        response = _aggregate_read_responses(ordered, compiled, report.status)
        return {
            "response": response.model_dump(mode="json"),
            "multi_intent_report": report.model_dump(mode="json"),
            "graph_steps": [*state.get("graph_steps", []), "multi_read_only"],
        }

    def _task_status(self, state: V29GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        principal = AccessPrincipal.model_validate(state["principal"])
        task_id = compiled.structured_request.get("task_id") or self._repository.latest_task_id(
            principal.tenant_id, state["conversation_id"]
        )
        task_state = None
        if task_id:
            candidate = CheckpointStore().load(str(task_id))
            if candidate.principal.tenant_id != principal.tenant_id:
                raise PermissionError("Task tenant does not match request tenant")
            task_state = candidate.model_dump(mode="json")
        return {
            "task_state": task_state or {},
            "graph_steps": [*state.get("graph_steps", []), "task_status"],
        }

    def _product_detail(self, state: V30GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        principal = AccessPrincipal.model_validate(state["principal"])
        ledger = ProductLedger(self._repository.database_path)
        resolver = EntityResolver(ledger, self._repository)
        resolution = resolver.resolve(
            principal.tenant_id,
            str(compiled.structured_request.get("query") or state["message"]),
            conversation_id=state["conversation_id"],
        )
        active_turn_id = state["active_turn_id"]
        clarification_round = 0
        while resolution.status == "ambiguous" and clarification_round < 3:
            choices = [
                f"{index + 1}. {item.title}（{item.sku or item.product_id}）"
                for index, item in enumerate(resolution.candidates)
            ]
            clarification = compiled.model_copy(deep=True)
            clarification.decision.intent = IntentName.clarify
            clarification.decision.original_intent = IntentName.product_detail
            clarification.assessment.mode = RequestMode.clarify
            clarification.assessment.clarification_round = clarification_round
            clarification.assessment.clarification_question = (
                "我找到了多个商品，请回复序号、SKU 或商品 ID：\n" + "\n".join(choices)
            )
            reply = interrupt(
                {
                    "kind": "entity_resolution",
                    "question": clarification.assessment.clarification_question,
                    "compiled_request": clarification.model_dump(mode="json"),
                    "candidates": [item.model_dump(mode="json") for item in resolution.candidates],
                }
            )
            active_turn_id = str(reply["turn_id"])
            answer = str(reply["message"]).strip()
            selected = _candidate_selection(answer, resolution.candidates)
            resolution = (
                resolver.resolve(principal.tenant_id, selected, conversation_id=state["conversation_id"])
                if selected
                else resolver.resolve(principal.tenant_id, answer, conversation_id=None)
            )
            clarification_round += 1
        detail = (
            ledger.detail(principal.tenant_id, resolution.product_id).model_dump(mode="json")
            if resolution.status == "resolved" and resolution.product_id
            else {}
        )
        if resolution.status == "resolved" and resolution.product_id:
            self._repository.set_active_product(
                principal.tenant_id, state["conversation_id"], resolution.product_id
            )
        return {
            "compiled_request": compiled.model_dump(mode="json"),
            "active_turn_id": active_turn_id,
            "entity_resolution": resolution.model_dump(mode="json"),
            "product_detail": detail,
            "graph_steps": [*state.get("graph_steps", []), "product_detail"],
        }

    def _product_performance(self, state: V30GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        principal = AccessPrincipal.model_validate(state["principal"])
        ledger = ProductLedger(self._repository.database_path)
        resolver = EntityResolver(ledger, self._repository)
        resolution = resolver.resolve(
            principal.tenant_id,
            str(compiled.structured_request.get("query") or state["message"]),
            conversation_id=state["conversation_id"],
        )
        active_turn_id = state["active_turn_id"]
        clarification_round = 0
        while resolution.status == "ambiguous" and clarification_round < 3:
            choices = [
                f"{index + 1}. {item.title}（{item.sku or item.product_id}）"
                for index, item in enumerate(resolution.candidates)
            ]
            clarification = compiled.model_copy(deep=True)
            clarification.decision.intent = IntentName.clarify
            clarification.decision.original_intent = IntentName.product_performance
            clarification.assessment.mode = RequestMode.clarify
            clarification.assessment.clarification_round = clarification_round
            clarification.assessment.clarification_question = (
                "我找到了多个商品。要查询销售表现，请回复序号、SKU 或商品 ID：\n"
                + "\n".join(choices)
            )
            reply = interrupt(
                {
                    "kind": "entity_resolution",
                    "question": clarification.assessment.clarification_question,
                    "compiled_request": clarification.model_dump(mode="json"),
                    "candidates": [item.model_dump(mode="json") for item in resolution.candidates],
                }
            )
            active_turn_id = str(reply["turn_id"])
            answer = str(reply["message"]).strip()
            selected = _candidate_selection(answer, resolution.candidates)
            resolution = (
                resolver.resolve(principal.tenant_id, selected, conversation_id=state["conversation_id"])
                if selected
                else resolver.resolve(principal.tenant_id, answer, conversation_id=None)
            )
            clarification_round += 1

        task_state: TaskState | None = None
        if resolution.status == "resolved" and resolution.product_id:
            self._repository.set_active_product(
                principal.tenant_id, state["conversation_id"], resolution.product_id
            )
            constraints = {
                key: value
                for key, value in compiled.structured_request.items()
                if key in {"start_date", "end_date", "period_label", "comparison_mode"}
            }
            constraints["product_id"] = resolution.product_id
            task_state = self._supervisor_factory().run_product_performance(
                state["message"],
                principal=principal,
                conversation_id=state["conversation_id"],
                turn_id=active_turn_id,
                constraints=constraints,
                context_seed=self._memory_service.context_seed(
                    principal.tenant_id, state["conversation_id"]
                ),
            )
            _attach_compiler_records(task_state, compiled)
            _attach_route_plan(task_state, compiled)
        return {
            "compiled_request": compiled.model_dump(mode="json"),
            "active_turn_id": active_turn_id,
            "entity_resolution": resolution.model_dump(mode="json"),
            "task_state": task_state.model_dump(mode="json") if task_state else {},
            "graph_steps": [*state.get("graph_steps", []), "product_performance"],
        }

    @staticmethod
    def _general_chat(state: V29GraphState) -> dict:
        return {"graph_steps": [*state.get("graph_steps", []), "general_chat"]}

    def _memory_candidate(self, state: V29GraphState) -> dict:
        compiled = CompiledRequest.model_validate(state["compiled_request"])
        principal = AccessPrincipal.model_validate(state["principal"])
        request = compiled.structured_request
        candidate = LongTermMemory(self._repository.database_path).propose(
            principal.tenant_id,
            scope=str(request.get("scope") or "global"),
            memory_type=str(request.get("memory_type") or "merchant_preference"),
            content=str(request["content"]),
            source="conversation_candidate",
            conflict_key=request.get("conflict_key"),
            metadata={
                "conversation_id": state["conversation_id"],
                "turn_id": state["active_turn_id"],
                "proposed_by": principal.subject_id,
            },
        )
        return {
            "memory_candidate": candidate.model_dump(mode="json"),
            "graph_steps": [*state.get("graph_steps", []), "memory_candidate"],
        }

    @staticmethod
    def _out_of_scope(state: V29GraphState) -> dict:
        return {"graph_steps": [*state.get("graph_steps", []), "out_of_scope"]}

    @staticmethod
    def _advisory(state: V29GraphState) -> dict:
        return {"graph_steps": [*state.get("graph_steps", []), "advisory"]}

    @staticmethod
    def _answer(state: V29GraphState) -> dict:
        from app.copilot.facade import ConversationFacade

        compiled = CompiledRequest.model_validate(state["compiled_request"])
        task_state = TaskState.model_validate(state["task_state"]) if state.get("task_state") else None
        if state.get("batch_run_report"):
            response = ConversationFacade.build_batch_response(
                compiled,
                BatchRunReport.model_validate(state["batch_run_report"]),
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
            )
        elif task_state and compiled.decision.intent in {
            IntentName.create_listing,
            IntentName.modify_listing,
            IntentName.market_research,
            IntentName.product_performance,
        }:
            response = ConversationFacade.build_response(task_state, compiled=compiled)
        elif compiled.decision.intent is IntentName.task_status:
            response = ConversationFacade.build_status_response(
                compiled,
                task_state=task_state,
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
            )
        elif compiled.decision.intent is IntentName.product_detail:
            response = ConversationFacade.build_product_response(
                compiled,
                detail=state.get("product_detail") or None,
                resolution=state.get("entity_resolution") or {},
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
            )
        elif compiled.decision.intent is IntentName.modify_listing:
            response = ConversationFacade.build_product_response(
                compiled,
                detail=None,
                resolution=state.get("entity_resolution") or {},
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
            )
        elif compiled.decision.intent is IntentName.product_performance:
            response = ConversationFacade.build_product_performance_not_found(
                compiled,
                resolution=state.get("entity_resolution") or {},
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
            )
        elif compiled.decision.intent is IntentName.remember_preference:
            response = ConversationFacade.build_memory_candidate_response(
                compiled,
                candidate=state.get("memory_candidate") or {},
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
            )
        else:
            response = ConversationFacade.build_non_task_response(
                compiled,
                conversation_id=state["conversation_id"],
                turn_id=state["active_turn_id"],
            )
        return {
            "response": response.model_dump(mode="json"),
            "graph_steps": [*state.get("graph_steps", []), "answer"],
        }


def _listing_goal(payload: dict[str, Any]) -> str:
    features = "、".join(payload.get("confirmed_features") or []) or "暂无补充"
    form = payload.get("confirmed_product_form") or "未确认"
    parts = [
        f"我要上架一款成本 {payload['cost']} 元的{payload['category']}，",
        f"目标售价 {payload['target_price']} 元，库存 {payload['inventory']} 件",
    ]
    if payload.get("target_audience"):
        parts.append(f"，主要面向{payload['target_audience']}")
    if payload.get("min_margin_rate") is not None:
        parts.append(f"，毛利率不能低于 {float(payload['min_margin_rate']) * 100:g}%")
    parts.append(f"。已确认的产品功能：{features}。已确认的产品形态：{form}。")
    if payload.get("operation_goal"):
        parts.append(f"运营目标：{payload['operation_goal']}。")
    return "".join(parts)


def _attach_compiler_records(state: TaskState, compiled: CompiledRequest) -> None:
    if compiled.compiler_model_records:
        state.model_records = [*compiled.compiler_model_records, *state.model_records]
    state.context_seed = {
        **state.context_seed,
        "semantic_compiler": {
            "protocol_version": compiled.compiler_protocol_version,
            "status": compiled.semantic_status,
            "diagnostics": [
                item.model_dump(mode="json") for item in compiled.semantic_diagnostics
            ],
            "field_evidence": [
                item.model_dump(mode="json")
                for item in compiled.assessment.field_evidence
            ],
        },
    }
    CheckpointStore().save(state)


class V28ConversationGraph(V29ConversationGraph):
    """Two-value compatibility adapter retained for v27/v28 regression tests."""

    def invoke(
        self,
        message: str,
        *,
        principal: AccessPrincipal,
        conversation_id: str = "conv_compatibility",
        turn_id: str = "turn_compatibility",
    ) -> tuple[CopilotResponse, list[str]]:
        response, steps, _compiled = super().invoke(
            message,
            principal=principal,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        if "listing_workflow" in steps:
            steps = ["receive", "legacy_listing_workflow", "answer"]
        return response, steps


class V30ConversationGraph(V29ConversationGraph):
    """Named v30 entry point; the v29 class remains as a compatibility alias."""


class V31ConversationGraph(V29ConversationGraph):
    """V31 entry point with product analytics intent routing."""


class V32ConversationGraph(V29ConversationGraph):
    """V32 entry point with template routing and read/write separation."""


class V33ConversationGraph(V29ConversationGraph):
    """V33 entry point with persistent memory and priority context assembly."""


def _candidate_selection(answer: str, candidates: list[Any]) -> str | None:
    if answer.isdigit():
        index = int(answer) - 1
        if 0 <= index < len(candidates):
            return str(candidates[index].product_id)
    for candidate in candidates:
        if candidate.product_id.lower() in answer.lower():
            return candidate.product_id
        if candidate.sku and candidate.sku.lower() in answer.lower():
            return candidate.sku
    return None


V27ConversationGraph = V28ConversationGraph


def _attach_route_plan(state: TaskState, compiled: CompiledRequest) -> None:
    if compiled.route_plan is None:
        return
    state.route_plan = compiled.route_plan.model_dump(mode="json")
    CheckpointStore().save(state)
    TraceRecorder(run_id=state.run_id).record_event(
        state.task_id,
        TraceEventType.plan_created,
        "orchestrator",
        "conversation_orchestrator",
        "route_plan",
        status="completed",
        details={
            **state.route_plan,
            "actual_agents": [
                node.agent_name
                for node in state.nodes.values()
                if node.status.value != "skipped"
            ],
        },
    )


def _aggregate_read_responses(
    responses: list[CopilotResponse],
    compiled: CompiledRequest,
    report_status: str,
) -> CopilotResponse:
    base = responses[0].model_copy(deep=True)
    steps = [
        step.model_copy(update={"step_id": f"intent_{index}_{step.step_id}"})
        for index, response in enumerate(responses, 1)
        for step in response.action_summary.steps
    ]
    actual_calls = sum(item.model_usage.actual_call_count for item in responses)
    stub_calls = sum(item.model_usage.stub_call_count for item in responses)
    recorded_calls = sum(item.model_usage.recorded_call_count for item in responses)
    providers = list(dict.fromkeys(
        provider for item in responses for provider in item.model_usage.providers_used
    ))
    completed = report_status == "completed"
    base.task_id = None
    base.run_id = None
    base.outcome = (
        CopilotOutcome.read_only_completed if completed else CopilotOutcome.technical_failed
    )
    base.intent = compiled.decision
    base.assessment = compiled.assessment
    base.route_plan = compiled.route_plan
    base.data_scope = list(dict.fromkeys(
        scope for item in responses for scope in item.data_scope
    ))
    base.entity_refs = list(dict.fromkeys(
        ref for item in responses for ref in item.entity_refs
    ))
    base.assistant_message = "\n\n".join(
        f"{index}. {response.assistant_message}"
        for index, response in enumerate(responses, 1)
    )
    base.understood_requirements = {
        "intent_count": len(compiled.intent_units),
        "execution": "parallel_read_group",
        "units": [unit.model_dump(mode="json") for unit in compiled.intent_units],
    }
    base.action_summary = ActionSummary(
        headline=f"已处理 {len(responses)} 个只读意图",
        steps=steps,
        completed_step_count=sum(step.status == "completed" for step in steps),
        total_step_count=len(steps),
        tool_call_count=sum(item.action_summary.tool_call_count for item in responses),
        trace_event_count=sum(item.action_summary.trace_event_count for item in responses),
        execution_performed=False,
    )
    base.panels = [panel for item in responses for panel in item.panels]
    base.model_usage = ModelUsageSummary(
        configured_provider=responses[0].model_usage.configured_provider,
        configured_model=responses[0].model_usage.configured_model,
        recorded_call_count=recorded_calls,
        actual_call_count=actual_calls,
        stub_call_count=stub_calls,
        mode="real_model" if actual_calls else "test_stub" if stub_calls else "no_model_call",
        providers_used=providers,
    )
    base.approval_required = False
    base.approval_state = "not_required"
    base.execution_plan_hash = None
    base.store_modified = False
    base.failure = next((item.failure for item in responses if item.failure), None)
    return base
