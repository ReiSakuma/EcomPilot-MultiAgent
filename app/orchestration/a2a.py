from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.orchestration.artifacts import Artifact


if TYPE_CHECKING:
    from app.orchestration.state import TaskNode, TaskState


A2A_PROTOCOL_VERSION = "1.0"


class A2AError(RuntimeError):
    safe_to_retry = False


class CapabilityNotFoundError(A2AError):
    pass


class A2AContractError(A2AError):
    pass


class A2ABudgetExceededError(A2AError):
    pass


class A2AStateTransitionError(A2AError):
    pass


class AgentCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_id: str = Field(min_length=3, max_length=80)
    input_artifact_types: tuple[str, ...] = ()
    output_artifact_type: str = Field(min_length=2, max_length=80)
    read_only: bool = True
    allowed_tools: tuple[str, ...] = ()


class AgentCard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_name: str = Field(min_length=3, max_length=80)
    protocol_version: Literal["1.0"] = A2A_PROTOCOL_VERSION
    capabilities: tuple[AgentCapability, ...]
    max_concurrency: int = Field(default=1, ge=1, le=16)


class CapabilityDirectory:
    """Deterministic capability discovery for registered specialist Agents."""

    def __init__(self, cards: list[AgentCard] | None = None) -> None:
        cards = cards or default_agent_cards()
        self._cards: dict[str, AgentCard] = {}
        self._routes: dict[str, tuple[str, AgentCapability]] = {}
        for card in cards:
            if card.agent_name in self._cards:
                raise A2AContractError(f"Duplicate Agent card: {card.agent_name}")
            self._cards[card.agent_name] = card
            for capability in card.capabilities:
                if capability.capability_id in self._routes:
                    raise A2AContractError(
                        f"Ambiguous capability route: {capability.capability_id}"
                    )
                self._routes[capability.capability_id] = (
                    card.agent_name,
                    capability,
                )

    def discover(
        self, capability_id: str, *, expected_agent: str | None = None
    ) -> tuple[AgentCard, AgentCapability]:
        route = self._routes.get(capability_id)
        if route is None:
            raise CapabilityNotFoundError(
                f"No Agent provides capability '{capability_id}'"
            )
        agent_name, capability = route
        if expected_agent is not None and agent_name != expected_agent:
            raise A2AContractError(
                f"Capability '{capability_id}' routes to '{agent_name}', "
                f"not '{expected_agent}'"
            )
        return self._cards[agent_name], capability

    def card(self, agent_name: str) -> AgentCard:
        try:
            return self._cards[agent_name]
        except KeyError as exc:
            raise CapabilityNotFoundError(
                f"Unknown Agent card '{agent_name}'"
            ) from exc

    def cards(self) -> list[AgentCard]:
        return list(self._cards.values())

    def validate_tool_registry(self, registry: Any) -> None:
        specs = registry.specs()
        for card in self._cards.values():
            for capability in card.capabilities:
                for tool_name in capability.allowed_tools:
                    spec = specs.get(tool_name)
                    if spec is None:
                        raise A2AContractError(
                            f"Capability '{capability.capability_id}' references "
                            f"unknown tool '{tool_name}'"
                        )
                    if (
                        spec.allowed_agents
                        and card.agent_name not in spec.allowed_agents
                    ):
                        raise A2AContractError(
                            f"Agent '{card.agent_name}' advertises unauthorized "
                            f"tool '{tool_name}'"
                        )


class A2ABudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_delegations: int = Field(default=12, ge=1, le=1000)
    max_delegations_per_agent: int = Field(default=3, ge=1, le=100)
    max_hops: int = Field(default=2, ge=1, le=10)
    max_fanout: int = Field(default=3, ge=1, le=32)


class A2ADelegationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = A2A_PROTOCOL_VERSION
    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex[:12]}")
    delegation_id: str = Field(default_factory=lambda: f"dlg_{uuid4().hex[:12]}")
    task_id: str
    tenant_id: str = Field(default="tenant_demo", min_length=3, max_length=80)
    conversation_id: str | None = None
    turn_id: str | None = None
    intent: str = "create_listing"
    risk_scope: Literal["none", "read", "write_plan", "write_execute"] = "read"
    capability_access: Literal["read", "write_plan", "write_execute"] = "read"
    approval_granted: bool = False
    sender_agent: str
    receiver_agent: str
    capability_id: str
    instruction: str
    input_state_version: int = Field(ge=0)
    input_artifact_refs: tuple[str, ...] = ()
    parent_delegation_id: str | None = None
    attempt: int = Field(default=1, ge=1, le=100)
    hop_count: int = Field(default=1, ge=1, le=10)
    idempotency_key: str
    created_at: datetime
    deadline_at: datetime


A2AStatus = Literal[
    "created",
    "accepted",
    "running",
    "completed",
    "rejected",
    "failed",
    "cancelled",
]


class A2ADelegationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: A2ADelegationRequest
    status: A2AStatus = "created"
    output_artifact_ref: str | None = None
    error: str | None = None
    updated_at: datetime


class A2ATransitionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: f"a2aevt_{uuid4().hex[:12]}")
    delegation_id: str
    task_id: str
    actor: str
    previous_status: A2AStatus | None
    current_status: A2AStatus
    reason: str | None = None
    created_at: datetime


_ALLOWED_TRANSITIONS: dict[A2AStatus, set[A2AStatus]] = {
    "created": {"accepted", "rejected", "cancelled"},
    "accepted": {"running", "rejected", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),
    "rejected": set(),
    "failed": set(),
    "cancelled": set(),
}


class A2ACoordinator:
    """Creates typed delegations and validates their state transitions."""

    def __init__(
        self,
        directory: CapabilityDirectory | None = None,
        *,
        delegation_timeout_seconds: float = 120.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.directory = directory or CapabilityDirectory()
        self.delegation_timeout_seconds = delegation_timeout_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._observer: Callable[[dict[str, Any]], None] | None = None

    def set_observer(self, observer: Callable[[dict[str, Any]], None] | None) -> None:
        self._observer = observer

    def assert_batch_budget(self, state: TaskState, nodes: list[TaskNode]) -> None:
        if len(nodes) > state.a2a_budget.max_fanout:
            raise A2ABudgetExceededError(
                f"A2A fanout {len(nodes)} exceeds {state.a2a_budget.max_fanout}"
            )

    def create_for_node(
        self, state: TaskState, node: TaskNode
    ) -> A2ADelegationRecord:
        if len(state.a2a_delegations) >= state.a2a_budget.max_delegations:
            raise A2ABudgetExceededError("A2A global delegation budget exhausted")
        capability_id = node.capability_id or capability_for_agent(node.agent_name)
        card, capability = self.directory.discover(
            capability_id, expected_agent=node.agent_name
        )
        active_count = sum(
            1
            for record in state.a2a_delegations.values()
            if record.request.receiver_agent == node.agent_name
            and record.status in {"created", "accepted", "running"}
        )
        if active_count >= card.max_concurrency:
            raise A2ABudgetExceededError(
                f"A2A concurrency limit reached for '{node.agent_name}'"
            )
        receiver_count = sum(
            1
            for record in state.a2a_delegations.values()
            if record.request.receiver_agent == node.agent_name
        )
        if receiver_count >= state.a2a_budget.max_delegations_per_agent:
            raise A2ABudgetExceededError(
                f"A2A delegation budget exhausted for '{node.agent_name}'"
            )

        refs = self._dependency_artifact_refs(state, node)
        observed_types = tuple(
            state.artifacts[artifact_id].artifact_type for artifact_id in refs
        )
        if sorted(observed_types) != sorted(capability.input_artifact_types):
            raise A2AContractError(
                f"Capability '{capability_id}' requires artifacts "
                f"{list(capability.input_artifact_types)}, received {list(observed_types)}"
            )

        previous_id = node.delegation_id
        attempt = 1 + sum(
            1
            for record in state.a2a_delegations.values()
            if record.request.receiver_agent == node.agent_name
            and record.request.capability_id == capability_id
        )
        hop_count = 1
        if hop_count > state.a2a_budget.max_hops:
            raise A2ABudgetExceededError("A2A hop budget exhausted")
        now = self._now()
        idempotency_key = _delegation_key(
            state.task_id,
            node.node_id,
            attempt,
            state.state_version,
            refs,
        )
        if any(
            record.request.idempotency_key == idempotency_key
            for record in state.a2a_delegations.values()
        ):
            raise A2AContractError("Duplicate A2A delegation idempotency key")
        request = A2ADelegationRequest(
            task_id=state.task_id,
            tenant_id=state.principal.tenant_id,
            conversation_id=state.conversation_id,
            turn_id=state.turn_id,
            intent=state.intent,
            risk_scope=_capability_access(capability_id, capability.read_only),
            capability_access=_capability_access(capability_id, capability.read_only),
            approval_granted=state.approved,
            sender_agent="supervisor",
            receiver_agent=node.agent_name,
            capability_id=capability_id,
            instruction=(
                f"Execute '{capability_id}' for task '{state.task_id}' using only "
                "the referenced artifacts and trusted task constraints."
            ),
            input_state_version=state.state_version,
            input_artifact_refs=refs,
            parent_delegation_id=previous_id,
            attempt=attempt,
            hop_count=hop_count,
            idempotency_key=idempotency_key,
            created_at=now,
            deadline_at=now + timedelta(seconds=self.delegation_timeout_seconds),
        )
        record = A2ADelegationRecord(request=request, updated_at=now)
        state.a2a_delegations[request.delegation_id] = record
        node.delegation_id = request.delegation_id
        self._append_event(
            state,
            record,
            actor="supervisor",
            previous=None,
            current="created",
        )
        return record

    def transition(
        self,
        state: TaskState,
        delegation_id: str,
        current: A2AStatus,
        *,
        actor: str,
        reason: str | None = None,
        output_artifact_ref: str | None = None,
    ) -> A2ADelegationRecord:
        record = state.a2a_delegations.get(delegation_id)
        if record is None:
            raise A2AContractError(f"Unknown delegation '{delegation_id}'")
        previous = record.status
        if current not in _ALLOWED_TRANSITIONS[previous]:
            raise A2AStateTransitionError(
                f"Invalid A2A transition {previous} -> {current}"
            )
        if actor not in {record.request.sender_agent, record.request.receiver_agent}:
            raise A2AContractError(
                f"Actor '{actor}' is not part of delegation '{delegation_id}'"
            )
        if self._now() > record.request.deadline_at and current not in {
            "failed",
            "cancelled",
        }:
            raise A2AContractError(f"Delegation '{delegation_id}' expired")
        updated = record.model_copy(
            update={
                "status": current,
                "output_artifact_ref": output_artifact_ref,
                "error": reason if current in {"failed", "rejected"} else None,
                "updated_at": self._now(),
            }
        )
        state.a2a_delegations[delegation_id] = updated
        self._append_event(
            state,
            updated,
            actor=actor,
            previous=previous,
            current=current,
            reason=reason,
        )
        return updated

    def validate_completion(
        self,
        state: TaskState,
        node: TaskNode,
        artifact: Artifact,
    ) -> None:
        if not node.delegation_id:
            raise A2AContractError(f"Node '{node.node_id}' has no A2A delegation")
        record = state.a2a_delegations[node.delegation_id]
        if record.status != "running":
            raise A2AStateTransitionError(
                f"Delegation must be running before completion, found {record.status}"
            )
        if self._now() > record.request.deadline_at:
            raise A2AContractError(
                f"Delegation '{record.request.delegation_id}' expired before completion"
            )
        _, capability = self.directory.discover(
            record.request.capability_id,
            expected_agent=node.agent_name,
        )
        if artifact.artifact_type != capability.output_artifact_type:
            raise A2AContractError(
                f"Capability '{capability.capability_id}' must produce "
                f"'{capability.output_artifact_type}', got '{artifact.artifact_type}'"
            )
        if artifact.producer != record.request.receiver_agent:
            raise A2AContractError("A2A artifact producer does not match receiver")
        if artifact.input_state_version != record.request.input_state_version:
            raise A2AContractError("A2A artifact state version does not match request")

    def complete(
        self, state: TaskState, node: TaskNode, artifact: Artifact
    ) -> A2ADelegationRecord:
        self.validate_completion(state, node, artifact)
        assert node.delegation_id is not None
        return self.transition(
            state,
            node.delegation_id,
            "completed",
            actor=node.agent_name,
            output_artifact_ref=artifact.artifact_id,
        )

    def fail_active(
        self, state: TaskState, node: TaskNode, error: Exception | str
    ) -> None:
        if not node.delegation_id:
            return
        record = state.a2a_delegations.get(node.delegation_id)
        if record is None or record.status not in {"created", "accepted", "running"}:
            return
        if record.status == "created":
            self.transition(
                state,
                node.delegation_id,
                "rejected",
                actor=node.agent_name,
                reason=str(error),
            )
            return
        if record.status == "accepted":
            self.transition(
                state,
                node.delegation_id,
                "running",
                actor=node.agent_name,
            )
        self.transition(
            state,
            node.delegation_id,
            "failed",
            actor=node.agent_name,
            reason=str(error),
        )

    def _dependency_artifact_refs(
        self, state: TaskState, node: TaskNode
    ) -> tuple[str, ...]:
        refs: list[str] = []
        for dependency_id in node.dependencies:
            dependency = state.nodes.get(dependency_id)
            if dependency is None:
                raise A2AContractError(
                    f"Unknown DAG dependency '{dependency_id}'"
                )
            artifact_id = state.latest_artifacts.get(dependency.agent_name)
            if artifact_id is None or artifact_id not in state.artifacts:
                raise A2AContractError(
                    f"Dependency '{dependency_id}' has no shared Artifact"
                )
            refs.append(artifact_id)
        for artifact_id in node.supplemental_artifact_refs:
            if artifact_id not in state.artifacts:
                raise A2AContractError(
                    f"Supplemental Artifact '{artifact_id}' does not exist"
                )
            refs.append(artifact_id)
        return tuple(dict.fromkeys(refs))

    def _append_event(
        self,
        state: TaskState,
        record: A2ADelegationRecord,
        *,
        actor: str,
        previous: A2AStatus | None,
        current: A2AStatus,
        reason: str | None = None,
    ) -> None:
        event = A2ATransitionEvent(
            delegation_id=record.request.delegation_id,
            task_id=record.request.task_id,
            actor=actor,
            previous_status=previous,
            current_status=current,
            reason=reason,
            created_at=self._now(),
        )
        state.a2a_events.append(event)
        if self._observer is None:
            return
        try:
            self._observer(
                {
                    "event_type": "a2a_message",
                    "component_type": "a2a",
                    "component_name": record.request.capability_id,
                    "agent_name": record.request.receiver_agent,
                    "step": f"a2a.{current}",
                    "status": current,
                    "details": {
                        "delegation_id": record.request.delegation_id,
                        "message_id": record.request.message_id,
                        "sender_agent": record.request.sender_agent,
                        "receiver_agent": record.request.receiver_agent,
                        "capability_id": record.request.capability_id,
                        "conversation_id": record.request.conversation_id,
                        "turn_id": record.request.turn_id,
                        "intent": record.request.intent,
                        "risk_scope": record.request.risk_scope,
                        "capability_access": record.request.capability_access,
                        "approval_granted": record.request.approval_granted,
                        "input_artifact_refs": list(
                            record.request.input_artifact_refs
                        ),
                        "output_artifact_ref": record.output_artifact_ref,
                        "attempt": record.request.attempt,
                        "hop_count": record.request.hop_count,
                        "previous_status": previous,
                    },
                    "error": {
                        "type": "A2ADelegationError",
                        "message": reason,
                    }
                    if current in {"failed", "rejected"} and reason
                    else None,
                }
            )
        except Exception:
            return

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise A2AContractError("A2A clock must return timezone-aware datetime")
        return value


def capability_for_agent(agent_name: str) -> str:
    mapping = {
        "market_agent": "market.research",
        "market_price_gate_agent": "market.price_assess",
        "listing_agent": "listing.compose",
        "strategy_agent": "strategy.plan",
        "review_agent": "risk.review",
        "browser_agent": "seller.execute",
    }
    try:
        return mapping[agent_name]
    except KeyError as exc:
        raise CapabilityNotFoundError(
            f"No default capability for Agent '{agent_name}'"
        ) from exc


def default_agent_cards() -> list[AgentCard]:
    return [
        AgentCard(
            agent_name="market_agent",
            capabilities=(
                AgentCapability(
                    capability_id="market.research",
                    output_artifact_type="research_evidence",
                    allowed_tools=(
                        "search_products",
                        "search_keywords",
                        "get_reviews",
                        "analyze_review_pain_points",
                        "analyze_feature_frequency",
                        "build_market_report",
                        "query_market_database",
                    ),
                ),
            ),
        ),
        AgentCard(
            agent_name="market_price_gate_agent",
            capabilities=(
                AgentCapability(
                    capability_id="market.price_assess",
                    input_artifact_types=("research_evidence",),
                    output_artifact_type="market_price_assessment",
                ),
            ),
        ),
        AgentCard(
            agent_name="listing_agent",
            capabilities=(
                AgentCapability(
                    capability_id="listing.compose",
                    input_artifact_types=("research_evidence", "market_price_assessment"),
                    output_artifact_type="listing",
                ),
                AgentCapability(
                    capability_id="listing.revise",
                    input_artifact_types=(
                        "research_evidence",
                        "market_price_assessment",
                        "risk_decision",
                    ),
                    output_artifact_type="listing",
                ),
            ),
        ),
        AgentCard(
            agent_name="strategy_agent",
            capabilities=(
                AgentCapability(
                    capability_id="strategy.plan",
                    input_artifact_types=("research_evidence", "market_price_assessment"),
                    output_artifact_type="strategy",
                    allowed_tools=(
                        "suggest_discount",
                        "calculate_margin",
                        "check_inventory",
                        "forecast_demand",
                        "query_campaign_history",
                        "analyze_competitor_price_trends",
                    ),
                ),
                AgentCapability(
                    capability_id="strategy.revise",
                    input_artifact_types=(
                        "research_evidence",
                        "market_price_assessment",
                        "risk_decision",
                    ),
                    output_artifact_type="strategy",
                    allowed_tools=("calculate_margin", "check_inventory"),
                ),
            ),
        ),
        AgentCard(
            agent_name="review_agent",
            capabilities=(
                AgentCapability(
                    capability_id="risk.review",
                    input_artifact_types=("listing", "strategy"),
                    output_artifact_type="risk_decision",
                ),
            ),
        ),
        AgentCard(
            agent_name="analytics_agent",
            capabilities=(
                AgentCapability(
                    capability_id="analytics.read",
                    output_artifact_type="analytics_report",
                    allowed_tools=(
                        "get_sales_metrics",
                        "compare_sales_periods",
                        "get_campaign_performance",
                        "get_inventory_history",
                    ),
                ),
            ),
        ),
        AgentCard(
            agent_name="browser_agent",
            capabilities=(
                AgentCapability(
                    capability_id="seller.execute",
                    input_artifact_types=("risk_decision",),
                    output_artifact_type="execution_receipt",
                    read_only=False,
                    allowed_tools=(
                        "browser_execute",
                        "browser_verify",
                        "get_seller_center_snapshot",
                    ),
                ),
            ),
        ),
    ]


def _capability_access(
    capability_id: str, read_only: bool
) -> Literal["read", "write_plan", "write_execute"]:
    if capability_id == "seller.execute":
        return "write_execute"
    if capability_id in {"listing.compose", "listing.revise", "strategy.plan", "strategy.revise", "risk.review"}:
        return "write_plan"
    return "read" if read_only else "write_execute"


def _delegation_key(
    task_id: str,
    node_id: str,
    attempt: int,
    state_version: int,
    refs: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {
            "task_id": task_id,
            "node_id": node_id,
            "attempt": attempt,
            "state_version": state_version,
            "artifact_refs": refs,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
