from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TraceEventType(str, Enum):
    run_started = "run_started"
    plan_created = "plan_created"
    node_started = "node_started"
    agent_completed = "agent_completed"
    model_call = "model_call"
    react_step = "react_step"
    tool_call = "tool_call"
    policy_decision = "policy_decision"
    a2a_message = "a2a_message"
    capability_token = "capability_token"
    semantic_correction = "semantic_correction"
    state_transition = "state_transition"
    checkpoint_saved = "checkpoint_saved"
    checkpoint_loaded = "checkpoint_loaded"
    run_resumed = "run_resumed"
    recovery_decision = "recovery_decision"
    working_memory = "working_memory"
    approval_waiting = "approval_waiting"
    error = "error"
    run_completed = "run_completed"


class TraceEvent(BaseModel):
    event_id: str
    sequence: int
    run_id: str
    task_id: str
    event_type: TraceEventType
    timestamp: datetime
    component_type: str
    component_name: str
    step: str
    status: str | None = None
    duration_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class RunSummary(BaseModel):
    run_id: str
    task_id: str = ""
    goal: str = ""
    status: str = "unknown"
    parent_run_id: str | None = None
    resume_count: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    event_count: int = 0
    agent_event_count: int = 0
    tool_call_count: int = 0
    model_call_count: int = 0
    error_count: int = 0
    failed_status_event_count: int = 0
    guardrail_event_count: int = 0
    agent_statuses: dict[str, str] = Field(default_factory=dict)
