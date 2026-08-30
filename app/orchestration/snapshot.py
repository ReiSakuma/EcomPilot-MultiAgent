from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict


class NodeSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    agent_name: str
    dependencies: tuple[str, ...]
    status: str
    retry_count: int


class StateSnapshot(BaseModel):
    """Immutable, serialization-backed view of state exposed to future Agent runtimes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0"
    task_id: str
    run_id: str
    goal: str
    status: str
    state_version: int
    checkpoint_version: int
    constraints_json: str
    agent_outputs_json: str
    nodes: tuple[NodeSnapshot, ...]
    artifact_refs: tuple[str, ...]

    @classmethod
    def capture(cls, state: Any) -> "StateSnapshot":
        return cls(
            task_id=state.task_id,
            run_id=state.run_id,
            goal=state.goal,
            status=state.status,
            state_version=state.state_version,
            checkpoint_version=state.checkpoint_version,
            constraints_json=json.dumps(
                state.constraints, ensure_ascii=False, sort_keys=True
            ),
            agent_outputs_json=json.dumps(
                state.agent_outputs, ensure_ascii=False, sort_keys=True
            ),
            nodes=tuple(
                NodeSnapshot(
                    node_id=node_id,
                    agent_name=node.agent_name,
                    dependencies=tuple(node.dependencies),
                    status=node.status.value,
                    retry_count=node.retry_count,
                )
                for node_id, node in state.nodes.items()
            ),
            artifact_refs=tuple(state.artifacts),
        )

    @property
    def constraints(self) -> Mapping[str, Any]:
        return MappingProxyType(json.loads(self.constraints_json))

    @property
    def agent_outputs(self) -> Mapping[str, Any]:
        return MappingProxyType(json.loads(self.agent_outputs_json))
