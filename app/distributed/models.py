from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PoolName = Literal["workflow", "model", "sql", "read_tool", "write_tool", "browser"]
JobStatus = Literal["queued", "leased", "completed", "failed", "dead"]
SagaStatus = Literal[
    "prepared", "executing", "completed", "compensating", "failed", "needs_attention"
]


class RuntimeJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    tenant_id: str
    pool: PoolName
    job_type: str
    idempotency_key: str
    payload_fingerprint: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_token: int = 0
    lease_expires_at: datetime | None = None
    available_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class LeaseGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: RuntimeJob
    worker_id: str
    lease_token: int
    lease_expires_at: datetime


class ExecutionPermit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saga_id: str
    tenant_id: str
    resource_id: str
    operation: str
    idempotency_key: str
    plan_hash: str
    owner_id: str
    fencing_token: int
    expected_version: int
    lease_expires_at: datetime
    replay_result: dict[str, Any] | None = None


class BusinessEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effect_id: str
    tenant_id: str
    resource_id: str
    operation: str
    idempotency_key: str
    plan_hash: str
    resource_version: int
    fencing_token: int
    result: dict[str, Any]
    confirmed_at: datetime


class SagaRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saga_id: str
    tenant_id: str
    resource_id: str
    operation: str
    idempotency_key: str
    plan_hash: str
    status: SagaStatus
    fencing_token: int
    expected_version: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime
