from __future__ import annotations

from app.orchestration.state import TaskState


class WorkingMemory:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def save(self, state: TaskState) -> None:
        self._tasks[state.task_id] = state

    def load(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def snapshot(self, state: TaskState) -> dict[str, object]:
        self.save(state)
        return {
            "task_id": state.task_id,
            "status": state.status,
            "completed_nodes": [
                node_id for node_id, node in state.nodes.items() if node.status == "completed"
            ],
            "pending_nodes": [
                node_id for node_id, node in state.nodes.items() if node.status == "pending"
            ],
            "agent_outputs": list(state.agent_outputs.keys()),
        }
