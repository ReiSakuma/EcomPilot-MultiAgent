from __future__ import annotations

from uuid import uuid4

from app.access.models import AccessPrincipal, default_principal
from app.agents.browser import BrowserAgent
from app.agents.analytics import AnalyticsAgent
from app.agents.listing import ListingAgent
from app.agents.market import MarketAgent
from app.agents.market_price_gate import MarketPriceGateAgent
from app.agents.review import ReviewAgent
from app.agents.strategy import StrategyAgent
from app.context.manager import ContextManager
from app.memory.long_term import seed_default_merchant_memory
from app.memory.working_memory import WorkingMemory
from app.config import (
    LLM_MODEL,
    LLM_PROVIDER,
    WORKFLOW_NODE_TIMEOUT_SECONDS,
    WORKFLOW_TIMEOUT_SECONDS,
)
from app.model.adapter import ModelAdapter
from app.model.policy import load_llm_policy
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.recovery import RecoveryCoordinator, RecoveryManager
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.react_loop import BoundedReactLoop, ReactLoopConfig
from app.orchestration.a2a import A2ACoordinator, CapabilityDirectory
from app.observability.recorder import TraceRecorder
from app.observability.schemas import TraceEventType
from app.orchestration.planner import Planner
from app.orchestration.state import TaskState
from app.safety.approval import Approval
from app.safety.policy_gateway import ToolPolicyGateway
from app.tools.governed_executor import GovernedToolExecutor
from app.tools.registry import GLOBAL_CIRCUITS, ToolRegistry
from app.security.capability_tokens import CapabilityAuthority
from app.security.ledger import SecurityLedger


class Supervisor:
    def __init__(self) -> None:
        self.security_ledger = SecurityLedger()
        self.capability_authority = CapabilityAuthority(ledger=self.security_ledger)
        self.tools = ToolRegistry(
            self.capability_authority,
            require_capability_token=True,
            circuit_registry=GLOBAL_CIRCUITS,
        )
        self.planner = Planner()
        self.context_manager = ContextManager()
        self.long_term_memory = seed_default_merchant_memory()
        self.working_memory = WorkingMemory()
        self.model_adapter = ModelAdapter(provider=LLM_PROVIDER, model=LLM_MODEL)
        self.llm_policy = load_llm_policy()
        self.tool_policy_gateway = ToolPolicyGateway()
        self.capability_directory = CapabilityDirectory()
        self.capability_directory.validate_tool_registry(self.tools)
        self.a2a_coordinator = A2ACoordinator(
            self.capability_directory,
            delegation_timeout_seconds=WORKFLOW_NODE_TIMEOUT_SECONDS,
        )
        self.governed_tools = GovernedToolExecutor(
            self.tools, self.tool_policy_gateway
        )
        self.react_loop = BoundedReactLoop(
            self.model_adapter,
            self.governed_tools,
            ReactLoopConfig(
                max_steps=min(
                    self.llm_policy.react_max_steps,
                    self.llm_policy.max_calls_per_agent,
                ),
                max_tool_calls=self.llm_policy.react_max_tool_calls,
                timeout_seconds=self.llm_policy.react_timeout_seconds,
                max_identical_actions=self.llm_policy.react_max_identical_actions,
                input_token_budget=self.llm_policy.react_input_token_budget,
                max_output_tokens=self.llm_policy.react_max_output_tokens,
                compression_trigger_ratio=(
                    self.llm_policy.react_compression_trigger_ratio
                ),
            ),
        )
        self.agents = {
            "market_agent": MarketAgent(
                self.tools,
                self.context_manager,
                self.long_term_memory,
                self.model_adapter,
                self.llm_policy,
                self.react_loop,
            ),
            "market_price_gate_agent": MarketPriceGateAgent(
                self.tools,
                self.context_manager,
                self.long_term_memory,
                self.model_adapter,
                self.llm_policy,
            ),
            "listing_agent": ListingAgent(
                self.tools, self.context_manager, self.long_term_memory, self.model_adapter, self.llm_policy
            ),
            "strategy_agent": StrategyAgent(
                self.tools,
                self.context_manager,
                self.long_term_memory,
                self.model_adapter,
                self.llm_policy,
                self.react_loop,
            ),
            "review_agent": ReviewAgent(
                self.tools, self.context_manager, self.long_term_memory, self.model_adapter, self.llm_policy
            ),
            "browser_agent": BrowserAgent(
                self.tools, self.context_manager, self.long_term_memory, self.model_adapter, self.llm_policy
            ),
            "analytics_agent": AnalyticsAgent(
                self.tools,
                self.context_manager,
                self.long_term_memory,
                self.model_adapter,
                self.llm_policy,
                self.react_loop,
            ),
        }

    def run(
        self,
        goal: str,
        approved: bool = False,
        approved_by: str | None = None,
        approval_reason: str | None = None,
        principal: AccessPrincipal | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        intent: str = "create_listing",
        entity_refs: list[str] | None = None,
        constraint_overrides: dict | None = None,
        context_seed: dict | None = None,
    ) -> TaskState:
        principal = principal or default_principal()
        state = self.planner.build_initial_state(
            goal,
            approved=approved,
            approved_by=approved_by,
            approval_reason=approval_reason,
            principal=principal,
        )
        state.conversation_id = conversation_id
        state.turn_id = turn_id
        state.intent = intent
        state.entity_refs = list(entity_refs or [])
        state.context_seed = dict(context_seed or {})
        if constraint_overrides:
            state.constraints.update(constraint_overrides)
        state.run_id = f"run_{uuid4().hex[:8]}"
        trace = TraceRecorder(run_id=state.run_id)

        self._bind_observers(state, trace)
        trace.record_event(
            state.task_id,
            TraceEventType.run_started,
            "orchestrator",
            "supervisor",
            "run",
            status="created",
            details={
                "goal": goal,
                "approved": approved,
                "approved_by": state.approved_by,
                "approval_reason": state.approval_reason,
                "parent_run_id": None,
                "resume_count": 0,
                "principal": principal.model_dump(mode="json"),
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "intent": intent,
                "entity_refs": state.entity_refs,
            },
        )
        trace.record_event(
            state.task_id,
            TraceEventType.plan_created,
            "orchestrator",
            "planner",
            "plan",
            status="completed",
            details={
                "constraints": state.constraints,
                "nodes": [node.model_dump(mode="json") for node in state.nodes.values()],
            },
        )
        return self._execute(state, trace)

    def run_market_research(
        self,
        goal: str,
        *,
        principal: AccessPrincipal | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        constraints: dict | None = None,
        context_seed: dict | None = None,
    ) -> TaskState:
        """Execute only the read-only Market node; no listing or browser node exists."""

        principal = principal or default_principal()
        state = self.planner.build_market_research_state(
            goal,
            principal=principal,
            constraints=constraints,
        )
        state.conversation_id = conversation_id
        state.turn_id = turn_id
        state.intent = "market_research"
        state.context_seed = dict(context_seed or {})
        state.run_id = f"run_{uuid4().hex[:8]}"
        trace = TraceRecorder(run_id=state.run_id)
        self._bind_observers(state, trace)
        trace.record_event(
            state.task_id,
            TraceEventType.run_started,
            "orchestrator",
            "supervisor",
            "market_read_only",
            status="created",
            details={
                "goal": goal,
                "principal": principal.model_dump(mode="json"),
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "intent": "market_research",
                "write_nodes": [],
            },
        )
        trace.record_event(
            state.task_id,
            TraceEventType.plan_created,
            "orchestrator",
            "planner",
            "market_read_only_plan",
            status="completed",
            details={
                "constraints": state.constraints,
                "nodes": [node.model_dump(mode="json") for node in state.nodes.values()],
            },
        )
        return self._execute(state, trace)

    def run_product_performance(
        self,
        goal: str,
        *,
        principal: AccessPrincipal | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        constraints: dict,
        context_seed: dict | None = None,
    ) -> TaskState:
        principal = principal or default_principal()
        state = self.planner.build_product_performance_state(
            goal,
            principal=principal,
            constraints=constraints,
        )
        state.conversation_id = conversation_id
        state.turn_id = turn_id
        state.context_seed = dict(context_seed or {})
        state.run_id = f"run_{uuid4().hex[:8]}"
        trace = TraceRecorder(run_id=state.run_id)
        self._bind_observers(state, trace)
        trace.record_event(
            state.task_id,
            TraceEventType.run_started,
            "orchestrator",
            "supervisor",
            "analytics_read_only",
            status="created",
            details={
                "goal": goal,
                "principal": principal.model_dump(mode="json"),
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "intent": "product_performance",
                "entity_refs": state.entity_refs,
                "write_nodes": [],
            },
        )
        trace.record_event(
            state.task_id,
            TraceEventType.plan_created,
            "orchestrator",
            "planner",
            "analytics_read_only_plan",
            status="completed",
            details={
                "constraints": state.constraints,
                "nodes": [node.model_dump(mode="json") for node in state.nodes.values()],
            },
        )
        return self._execute(state, trace)

    def resume(
        self,
        task_id: str,
        *,
        approval: Approval | None = None,
        retry_node: str | None = None,
        constraint_updates: dict | None = None,
        expected_checkpoint_version: int | None = None,
        requested_by: str | None = None,
        reason: str | None = None,
        turn_id: str | None = None,
    ) -> TaskState:
        with RecoveryCoordinator.claim(task_id):
            checkpoint_store = CheckpointStore()
            state = checkpoint_store.load(
                task_id, expected_version=expected_checkpoint_version
            )
            loaded_version = state.checkpoint_version
            new_run_id = f"run_{uuid4().hex[:8]}"
            state, recovery = RecoveryManager().prepare(
                state,
                new_run_id,
                approval=approval,
                retry_node=retry_node,
                constraint_updates=constraint_updates,
                requested_by=requested_by,
                reason=reason,
            )
            if turn_id is not None:
                state.turn_id = turn_id
            trace = TraceRecorder(run_id=state.run_id)
            self._bind_observers(state, trace)
            trace.record_event(
                state.task_id,
                TraceEventType.run_started,
                "orchestrator",
                "supervisor",
                "resume",
                status="recovering",
                details={
                    "goal": state.goal,
                    "approved": state.approved,
                    "approved_by": state.approved_by,
                    "approval_reason": state.approval_reason,
                    "parent_run_id": state.parent_run_id,
                    "resume_count": state.resume_count,
                },
            )
            trace.record_event(
                state.task_id,
                TraceEventType.checkpoint_loaded,
                "storage",
                "checkpoint_store",
                "resume",
                status="completed",
                details={"checkpoint_version": loaded_version},
            )
            trace.record_event(
                state.task_id,
                TraceEventType.run_resumed,
                "orchestrator",
                "recovery_manager",
                recovery.action,
                status="completed",
                details=recovery.model_dump(mode="json"),
            )
            trace.record_event(
                state.task_id,
                TraceEventType.recovery_decision,
                "orchestrator",
                "recovery_manager",
                "restart_nodes",
                status="completed",
                details={
                    "restarted_nodes": recovery.restarted_nodes,
                    "preserved_nodes": [
                        node_id
                        for node_id, node in state.nodes.items()
                        if node.status.value == "completed"
                    ],
                    "constraint_updates": constraint_updates or {},
                },
            )
            return self._execute(state, trace, checkpoint_store=checkpoint_store)

    def _bind_observers(self, state: TaskState, trace: TraceRecorder) -> None:
        def observe(event: dict) -> None:
            trace.record_event(
                state.task_id,
                event["event_type"],
                event["component_type"],
                event["component_name"],
                event["step"],
                status=event.get("status"),
                duration_ms=event.get("duration_ms"),
                details={"agent_name": event.get("agent_name"), **event.get("details", {})},
                error=event.get("error"),
            )

        self.tools.set_observer(observe)
        self.model_adapter.set_observer(observe)
        self.tool_policy_gateway.set_observer(observe)
        self.react_loop.set_observer(observe)
        self.a2a_coordinator.set_observer(observe)
        self.capability_authority.set_observer(observe)

    def _execute(
        self,
        state: TaskState,
        trace: TraceRecorder,
        checkpoint_store: CheckpointStore | None = None,
    ) -> TaskState:
        executor = WorkflowExecutor(
            agents=self.agents,
            tools=self.tools,
            trace=trace,
            checkpoint_store=checkpoint_store or CheckpointStore(),
            working_memory=self.working_memory,
            max_steps=12,
            node_timeout_seconds=WORKFLOW_NODE_TIMEOUT_SECONDS,
            workflow_timeout_seconds=WORKFLOW_TIMEOUT_SECONDS,
            a2a_coordinator=self.a2a_coordinator,
            capability_authority=self.capability_authority,
        )
        return executor.run(state)
