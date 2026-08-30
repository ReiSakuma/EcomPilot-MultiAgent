from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import PROJECT_VERSION
from app.conversations.repository import CONVERSATION_SCHEMA_VERSION
from app.orchestration.a2a import A2A_PROTOCOL_VERSION
from app.orchestration.artifacts import ARTIFACT_SCHEMA_VERSION
from app.sandbox.runner import SANDBOX_PROTOCOL_VERSION


class ContractVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    compatibility: Literal["backward_default", "strict", "database_migration"]
    source: str
    owner: str


class ProtocolManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_version: Literal["1.0"] = "1.0"
    release: Literal["v39-chaos-readiness"] = "v39-chaos-readiness"
    project_version: str
    contracts: tuple[ContractVersion, ...]


def build_protocol_manifest() -> ProtocolManifest:
    """Single source of truth for contracts crossing module or persistence boundaries."""

    return ProtocolManifest(
        project_version=PROJECT_VERSION,
        contracts=(
            ContractVersion(
                name="conversation_database",
                version=str(CONVERSATION_SCHEMA_VERSION),
                compatibility="database_migration",
                source="app/conversations/repository.py",
                owner="conversation",
            ),
            ContractVersion(
                name="task_state",
                version="1.1",
                compatibility="backward_default",
                source="app/orchestration/state.py",
                owner="orchestration",
            ),
            ContractVersion(
                name="copilot_response",
                version="1.7",
                compatibility="backward_default",
                source="app/copilot/schemas.py",
                owner="presentation",
            ),
            ContractVersion(
                name="request_compiler",
                version="1.2",
                compatibility="backward_default",
                source="app/copilot/intents.py",
                owner="routing",
            ),
            ContractVersion(
                name="route_plan",
                version="1.1",
                compatibility="backward_default",
                source="app/copilot/intents.py",
                owner="routing",
            ),
            ContractVersion(
                name="conversation_summary",
                version="2.0",
                compatibility="database_migration",
                source="app/memory/conversation.py",
                owner="context",
            ),
            ContractVersion(
                name="context_budget",
                version="1.0",
                compatibility="strict",
                source="app/context/budget.py",
                owner="context",
            ),
            ContractVersion(
                name="handoff",
                version="1.1",
                compatibility="backward_default",
                source="app/orchestration/handoff.py",
                owner="a2a",
            ),
            ContractVersion(
                name="artifact",
                version=ARTIFACT_SCHEMA_VERSION,
                compatibility="strict",
                source="app/orchestration/artifacts.py",
                owner="artifact",
            ),
            ContractVersion(
                name="market_price_assessment",
                version="market-price-gate-v1",
                compatibility="strict",
                source="app/tools/market_price_gate.py",
                owner="pricing",
            ),
            ContractVersion(
                name="failure_envelope",
                version="1.1",
                compatibility="backward_default",
                source="app/orchestration/failures.py",
                owner="failure",
            ),
            ContractVersion(
                name="tool_spec",
                version="2.0",
                compatibility="backward_default",
                source="app/tools/schemas.py",
                owner="reliability",
            ),
            ContractVersion(
                name="reliability",
                version="1.0",
                compatibility="strict",
                source="app/reliability/models.py",
                owner="reliability",
            ),
            ContractVersion(
                name="a2a",
                version=A2A_PROTOCOL_VERSION,
                compatibility="strict",
                source="app/orchestration/a2a.py",
                owner="a2a",
            ),
            ContractVersion(
                name="sandbox",
                version=SANDBOX_PROTOCOL_VERSION,
                compatibility="strict",
                source="app/sandbox/runner.py",
                owner="security",
            ),
            ContractVersion(
                name="run_bundle",
                version="2.5",
                compatibility="backward_default",
                source="scripts/export_run_bundle.py",
                owner="observability",
            ),
            ContractVersion(
                name="checkpoint_compatibility_diagnostic",
                version="1.0",
                compatibility="backward_default",
                source="app/release/compatibility.py",
                owner="release",
            ),
            ContractVersion(
                name="durable_job_queue",
                version="1.1",
                compatibility="database_migration",
                source="app/distributed/runtime.py",
                owner="runtime",
            ),
            ContractVersion(
                name="worker_lease_fencing",
                version="1.0",
                compatibility="strict",
                source="app/distributed/runtime.py",
                owner="runtime",
            ),
            ContractVersion(
                name="execution_saga_outbox",
                version="1.0",
                compatibility="database_migration",
                source="app/distributed/runtime.py",
                owner="execution",
            ),
            ContractVersion(
                name="worker_bulkhead",
                version="1.0",
                compatibility="strict",
                source="app/distributed/bulkhead.py",
                owner="reliability",
            ),
            ContractVersion(
                name="terminal_outcome",
                version="1.0",
                compatibility="strict",
                source="app/operations/terminal.py",
                owner="operations",
            ),
            ContractVersion(
                name="chaos_experiment",
                version="1.0",
                compatibility="strict",
                source="app/operations/chaos.py",
                owner="operations",
            ),
            ContractVersion(
                name="operational_slo",
                version="1.0",
                compatibility="strict",
                source="app/operations/assessment.py",
                owner="operations",
            ),
        ),
    )
