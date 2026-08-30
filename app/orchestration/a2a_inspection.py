from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.orchestration.a2a import A2A_PROTOCOL_VERSION, CapabilityDirectory
from app.orchestration.state import TaskState


def build_capability_catalog(
    directory: CapabilityDirectory | None = None,
) -> dict[str, Any]:
    """Return the public, read-only projection of registered Agent capabilities."""

    directory = directory or CapabilityDirectory()
    cards: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for card in directory.cards():
        capabilities = []
        for capability in card.capabilities:
            capability_payload = {
                "capability_id": capability.capability_id,
                "input_artifact_types": list(capability.input_artifact_types),
                "output_artifact_type": capability.output_artifact_type,
                "read_only": capability.read_only,
                "allowed_tools": list(capability.allowed_tools),
            }
            capabilities.append(capability_payload)
            routes.append(
                {
                    "capability_id": capability.capability_id,
                    "agent_name": card.agent_name,
                    **capability_payload,
                }
            )
        cards.append(
            {
                "agent_name": card.agent_name,
                "protocol_version": card.protocol_version,
                "max_concurrency": card.max_concurrency,
                "capabilities": capabilities,
            }
        )
    return {
        "protocol_version": A2A_PROTOCOL_VERSION,
        "transport": "in_process",
        "routing_mode": "deterministic_capability_dag",
        "state_exchange": "artifact_references",
        "agent_count": len(cards),
        "capability_count": len(routes),
        "cards": cards,
        "routes": routes,
    }


def build_task_collaboration_summary(state: TaskState) -> dict[str, Any]:
    """Project one checkpoint into an auditable A2A collaboration view."""

    records = sorted(
        state.a2a_delegations.values(),
        key=lambda record: (record.request.created_at, record.request.delegation_id),
    )
    status_counts = Counter(record.status for record in records)
    per_agent = Counter(record.request.receiver_agent for record in records)
    consumers: dict[str, list[str]] = defaultdict(list)
    producers: dict[str, str] = {}
    for record in records:
        for artifact_id in record.request.input_artifact_refs:
            consumers[artifact_id].append(record.request.delegation_id)
        if record.output_artifact_ref:
            producers[record.output_artifact_ref] = record.request.delegation_id

    artifacts = []
    artifact_ids = set(state.artifacts)
    for artifact in sorted(
        state.artifacts.values(), key=lambda item: (item.created_at, item.artifact_id)
    ):
        artifacts.append(
            {
                "artifact_id": artifact.artifact_id,
                "artifact_type": artifact.artifact_type,
                "producer": artifact.producer,
                "producer_delegation_id": producers.get(artifact.artifact_id),
                "input_state_version": artifact.input_state_version,
                "confidence": artifact.confidence,
                "parent_artifact_refs": [
                    ref for ref in artifact.evidence_refs if ref in artifact_ids
                ],
                "external_evidence_ref_count": sum(
                    1 for ref in artifact.evidence_refs if ref not in artifact_ids
                ),
                "consumer_delegation_ids": consumers.get(artifact.artifact_id, []),
                "content_hash": artifact.content_hash,
                "created_at": artifact.created_at.isoformat(),
            }
        )

    delegations = []
    for record in records:
        request = record.request
        delegations.append(
            {
                "delegation_id": request.delegation_id,
                "message_id": request.message_id,
                "tenant_id": request.tenant_id,
                "parent_delegation_id": request.parent_delegation_id,
                "capability_id": request.capability_id,
                "sender_agent": request.sender_agent,
                "receiver_agent": request.receiver_agent,
                "status": record.status,
                "attempt": request.attempt,
                "hop_count": request.hop_count,
                "input_state_version": request.input_state_version,
                "input_artifact_refs": list(request.input_artifact_refs),
                "output_artifact_ref": record.output_artifact_ref,
                "idempotency_key": request.idempotency_key,
                "created_at": request.created_at.isoformat(),
                "deadline_at": request.deadline_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
                "duration_ms": round(
                    (record.updated_at - request.created_at).total_seconds() * 1000,
                    3,
                ),
                "error": record.error,
            }
        )

    events = [
        {
            "event_id": event.event_id,
            "delegation_id": event.delegation_id,
            "actor": event.actor,
            "previous_status": event.previous_status,
            "current_status": event.current_status,
            "reason": event.reason,
            "created_at": event.created_at.isoformat(),
        }
        for event in state.a2a_events
    ]
    budget = state.a2a_budget
    return {
        "protocol_version": A2A_PROTOCOL_VERSION,
        "task_id": state.task_id,
        "run_id": state.run_id,
        "task_status": state.status,
        "checkpoint_version": state.checkpoint_version,
        "summary": {
            "delegation_count": len(records),
            "transition_count": len(events),
            "artifact_count": len(artifacts),
            "status_counts": dict(sorted(status_counts.items())),
            "retry_count": sum(max(0, record.request.attempt - 1) for record in records),
            "failed_count": sum(
                status_counts[status] for status in ("failed", "rejected", "cancelled")
            ),
        },
        "budget": {
            **budget.model_dump(mode="json"),
            "delegations_used": len(records),
            "delegations_remaining": max(0, budget.max_delegations - len(records)),
            "per_agent_used": dict(sorted(per_agent.items())),
        },
        "dag": [
            {
                "node_id": node.node_id,
                "agent_name": node.agent_name,
                "capability_id": node.capability_id,
                "dependencies": list(node.dependencies),
                "status": node.status.value,
                "active_delegation_id": node.delegation_id,
            }
            for node in state.nodes.values()
        ],
        "delegations": delegations,
        "artifacts": artifacts,
        "events": events,
    }
