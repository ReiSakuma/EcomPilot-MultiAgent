from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.access.models import AccessPrincipal
from app.copilot.batch_execution import BatchExecutionReport
from app.distributed.models import JobStatus
from app.distributed.runtime import DistributedRuntime


class BatchExecutionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_job_id: str
    operation: Literal["approve", "retry"]
    item_ids: list[str] = Field(min_length=1, max_length=5)
    expected_checkpoint_versions: dict[str, int] = Field(default_factory=dict)
    execution_generations: dict[str, int] = Field(default_factory=dict)
    principal: AccessPrincipal


class BatchExecutionDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_job_id: str
    batch_job_id: str
    status: JobStatus
    replayed: bool
    status_url: str


class BatchExecutionJobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_job_id: str
    batch_job_id: str
    status: JobStatus
    operation: Literal["approve", "retry"]
    execution_generations: dict[str, int] = Field(default_factory=dict)
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    is_latest: bool
    created_at: datetime
    updated_at: datetime
    result: BatchExecutionReport | None = None
    error: str | None = None


BatchExecutor = Callable[[BatchExecutionJobRequest], BatchExecutionReport]


class BatchExecutionDispatcher:
    """Durable queue adapter; it does not own approval or browser policy."""

    JOB_TYPE = "batch_store_execution"

    def __init__(self, runtime: DistributedRuntime | None = None) -> None:
        self.runtime = runtime or DistributedRuntime()

    def enqueue(
        self,
        request: BatchExecutionJobRequest,
        *,
        client_request_id: str,
    ) -> BatchExecutionDispatch:
        identity = json.dumps(
            {
                "batch_job_id": request.batch_job_id,
                "operation": request.operation,
                "item_ids": sorted(request.item_ids),
                "execution_generations": {
                    item_id: request.execution_generations.get(item_id, 0)
                    for item_id in sorted(request.item_ids)
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        logical_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        job, replayed = self.runtime.enqueue(
            tenant_id=request.principal.tenant_id,
            pool="browser",
            job_type=self.JOB_TYPE,
            idempotency_key=f"batch:{request.batch_job_id}:{request.operation}:{logical_key}",
            payload=request.model_dump(mode="json"),
            max_attempts=3,
        )
        return BatchExecutionDispatch(
            runtime_job_id=job.job_id,
            batch_job_id=request.batch_job_id,
            status=job.status,
            replayed=replayed,
            status_url=f"/api/copilot/batch-executions/{job.job_id}",
        )

    def run_once(self, *, worker_id: str, executor: BatchExecutor):
        def handle(payload: dict[str, Any]) -> dict[str, Any]:
            request = BatchExecutionJobRequest.model_validate(payload)
            return executor(request).model_dump(mode="json")

        return self.runtime.run_once(
            worker_id=worker_id,
            pool="browser",
            handlers={self.JOB_TYPE: handle},
        )

    def status(self, runtime_job_id: str, *, tenant_id: str) -> BatchExecutionJobStatus:
        job = self.runtime.get_job(runtime_job_id, tenant_id=tenant_id)
        if job.job_type != self.JOB_TYPE:
            raise KeyError("Batch execution job not found")
        request = BatchExecutionJobRequest.model_validate(job.payload)
        latest = self.latest(request.batch_job_id, tenant_id=tenant_id)
        return self._status(job, request=request, is_latest=(
            latest is not None and latest.runtime_job_id == job.job_id
        ))

    def latest(
        self, batch_job_id: str, *, tenant_id: str
    ) -> BatchExecutionJobStatus | None:
        jobs = self.runtime.list_jobs(
            tenant_id=tenant_id,
            job_type=self.JOB_TYPE,
            idempotency_prefix=f"batch:{batch_job_id}:",
            limit=50,
        )
        for job in jobs:
            request = BatchExecutionJobRequest.model_validate(job.payload)
            if request.batch_job_id == batch_job_id:
                return self._status(job, request=request, is_latest=True)
        return None

    @staticmethod
    def _status(job, *, request: BatchExecutionJobRequest, is_latest: bool):
        return BatchExecutionJobStatus(
            runtime_job_id=job.job_id,
            batch_job_id=request.batch_job_id,
            status=job.status,
            operation=request.operation,
            execution_generations=request.execution_generations,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            is_latest=is_latest,
            created_at=job.created_at,
            updated_at=job.updated_at,
            result=(
                BatchExecutionReport.model_validate(job.result)
                if job.result is not None and job.status == "completed"
                else None
            ),
            error=job.error,
        )
