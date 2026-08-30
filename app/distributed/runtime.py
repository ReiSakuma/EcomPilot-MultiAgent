from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from app.config import (
    DISTRIBUTED_RUNTIME_DATABASE_PATH,
    RUNTIME_GLOBAL_QUEUE_LIMIT,
    RUNTIME_LEASE_SECONDS,
    RUNTIME_TENANT_QUEUE_LIMIT,
    RUNTIME_TENANT_RATE_PER_MINUTE,
)
from app.distributed.bulkhead import BulkheadRegistry, GLOBAL_BULKHEADS
from app.distributed.models import (
    BusinessEffect,
    ExecutionPermit,
    LeaseGrant,
    PoolName,
    RuntimeJob,
    SagaRecord,
)


RUNTIME_SCHEMA_VERSION = 2


class RuntimeProtocolError(RuntimeError):
    pass


class QueueBackpressureError(RuntimeProtocolError):
    pass


class TenantRateLimitError(RuntimeProtocolError):
    pass


class RuntimeIdempotencyConflict(RuntimeProtocolError):
    pass


class StaleLeaseError(RuntimeProtocolError):
    pass


class ResourceBusyError(RuntimeProtocolError):
    pass


class OptimisticVersionConflict(RuntimeProtocolError):
    pass


class StaleFencingTokenError(RuntimeProtocolError):
    pass


class DistributedRuntime:
    """SQLite reference runtime for queue ownership and effectively-once effects.

    SQLite is deliberately used for the interview build: its ``BEGIN IMMEDIATE``
    transaction gives multiple local Worker processes one durable arbitration point.
    The protocol maps directly to PostgreSQL row locks in a production deployment.
    """

    _migration_lock = RLock()

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        global_queue_limit: int = RUNTIME_GLOBAL_QUEUE_LIMIT,
        tenant_queue_limit: int = RUNTIME_TENANT_QUEUE_LIMIT,
        tenant_rate_per_minute: int = RUNTIME_TENANT_RATE_PER_MINUTE,
        lease_seconds: int = RUNTIME_LEASE_SECONDS,
        bulkheads: BulkheadRegistry | None = None,
    ) -> None:
        self.database_path = database_path or DISTRIBUTED_RUNTIME_DATABASE_PATH
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.global_queue_limit = max(1, global_queue_limit)
        self.tenant_queue_limit = max(1, tenant_queue_limit)
        self.tenant_rate_per_minute = max(1, tenant_rate_per_minute)
        self.lease_seconds = max(1, lease_seconds)
        self.bulkheads = bulkheads or GLOBAL_BULKHEADS
        self.migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._migration_lock, self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_jobs (
                    job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    pool TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','leased','completed','failed','dead')),
                    priority INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    lease_owner TEXT,
                    lease_token INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at TEXT,
                    available_at TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_jobs_dispatch
                    ON runtime_jobs(pool, status, available_at, priority DESC, created_at);
                CREATE TABLE IF NOT EXISTS tenant_dispatch_state (
                    tenant_id TEXT PRIMARY KEY,
                    served_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenant_rate_windows (
                    tenant_id TEXT PRIMARY KEY,
                    window_started_at TEXT NOT NULL,
                    request_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resource_guards (
                    tenant_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    resource_version INTEGER NOT NULL DEFAULT 0,
                    fence_counter INTEGER NOT NULL DEFAULT 0,
                    active_token INTEGER,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, resource_id)
                );
                CREATE TABLE IF NOT EXISTS execution_sagas (
                    saga_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'prepared','executing','completed','compensating','failed','needs_attention'
                    )),
                    fencing_token INTEGER NOT NULL,
                    expected_version INTEGER NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_execution_sagas_status
                    ON execution_sagas(tenant_id, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS business_effects (
                    effect_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    resource_version INTEGER NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    UNIQUE(tenant_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_business_effect_resource
                    ON business_effects(tenant_id, resource_id, resource_version);
                CREATE TABLE IF NOT EXISTS transactional_outbox (
                    outbox_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','publishing','published','failed')),
                    lease_owner TEXT,
                    lease_token INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    UNIQUE(tenant_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_publish
                    ON transactional_outbox(status, created_at);
                CREATE TABLE IF NOT EXISTS runtime_metric_events (
                    metric_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    dimensions TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_metrics_tenant_time
                    ON runtime_metric_events(tenant_id, metric_name, created_at DESC);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO runtime_schema_migrations(version, applied_at) VALUES(?, ?)",
                (RUNTIME_SCHEMA_VERSION, _now()),
            )

    def enqueue(
        self,
        *,
        tenant_id: str,
        pool: PoolName,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
        enforce_rate_limit: bool = True,
    ) -> tuple[RuntimeJob, bool]:
        fingerprint = _fingerprint(payload)
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM runtime_jobs WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_fingerprint"] != fingerprint:
                    raise RuntimeIdempotencyConflict(
                        "The idempotency key was already used for a different runtime payload"
                    )
                self._record_metric(
                    connection, tenant_id, "idempotency_replay", 1,
                    {"pool": pool, "job_type": job_type},
                )
                connection.commit()
                return _job(existing), True
            self._check_backpressure(connection, tenant_id)
            if enforce_rate_limit:
                self._consume_rate_slot(connection, tenant_id, now)
            job_id = f"job_{uuid4().hex[:16]}"
            scheduled = (available_at or datetime.now(timezone.utc)).isoformat()
            connection.execute(
                """INSERT INTO runtime_jobs(
                    job_id, tenant_id, pool, job_type, idempotency_key,
                    payload_fingerprint, payload, status, priority, max_attempts,
                    available_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    tenant_id,
                    pool,
                    job_type,
                    idempotency_key,
                    fingerprint,
                    _json(payload),
                    priority,
                    max(1, max_attempts),
                    scheduled,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            self._record_metric(
                connection, tenant_id, "queue_enqueued", 1,
                {"pool": pool, "job_type": job_type},
            )
            connection.commit()
        return _job(row), False

    def lease_next(
        self,
        *,
        worker_id: str,
        pool: PoolName,
        lease_seconds: int | None = None,
    ) -> LeaseGrant | None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds or self.lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reclaim_expired(connection, now)
            row = connection.execute(
                """SELECT j.* FROM runtime_jobs j
                LEFT JOIN tenant_dispatch_state t ON t.tenant_id=j.tenant_id
                WHERE j.pool=? AND j.status='queued' AND j.available_at<=?
                ORDER BY COALESCE(t.served_count, 0) ASC,
                         j.priority DESC, j.created_at ASC
                LIMIT 1""",
                (pool, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            token = int(row["lease_token"]) + 1
            changed = connection.execute(
                """UPDATE runtime_jobs SET status='leased', lease_owner=?, lease_token=?,
                    lease_expires_at=?, attempts=attempts+1, updated_at=?
                WHERE job_id=? AND status='queued'""",
                (worker_id, token, expires, now, row["job_id"]),
            ).rowcount
            if changed != 1:
                connection.rollback()
                return None
            connection.execute(
                """INSERT INTO tenant_dispatch_state(tenant_id, served_count, updated_at)
                VALUES(?, 1, ?) ON CONFLICT(tenant_id) DO UPDATE SET
                served_count=served_count+1, updated_at=excluded.updated_at""",
                (row["tenant_id"], now),
            )
            leased = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (row["job_id"],)
            ).fetchone()
            queue_wait_ms = max(
                0.0,
                (now_dt - datetime.fromisoformat(str(row["created_at"]))).total_seconds()
                * 1000,
            )
            self._record_metric(
                connection, str(row["tenant_id"]), "queue_wait_ms", queue_wait_ms,
                {"pool": pool, "job_type": str(row["job_type"])},
            )
            connection.commit()
        job = _job(leased)
        return LeaseGrant(
            job=job,
            worker_id=worker_id,
            lease_token=token,
            lease_expires_at=expires,
        )

    def heartbeat(self, grant: LeaseGrant, *, lease_seconds: int | None = None) -> LeaseGrant:
        now = _now()
        expires = (
            datetime.now(timezone.utc)
            + timedelta(seconds=lease_seconds or self.lease_seconds)
        ).isoformat()
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE runtime_jobs SET lease_expires_at=?, updated_at=?
                WHERE job_id=? AND status='leased' AND lease_owner=? AND lease_token=?
                AND lease_expires_at>?""",
                (expires, now, grant.job.job_id, grant.worker_id, grant.lease_token, now),
            ).rowcount
        if changed != 1:
            raise StaleLeaseError("Worker no longer owns this job lease")
        return grant.model_copy(update={"lease_expires_at": expires})

    def complete(self, grant: LeaseGrant, result: dict[str, Any]) -> RuntimeJob:
        return self._finish_job(grant, status="completed", result=result)

    def fail(
        self,
        grant: LeaseGrant,
        error: str,
        *,
        retry_delay_seconds: float = 0,
    ) -> RuntimeJob:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._owned_job(connection, grant)
            retry = int(row["attempts"]) < int(row["max_attempts"])
            status = "queued" if retry else "dead"
            available = (
                datetime.now(timezone.utc) + timedelta(seconds=max(0, retry_delay_seconds))
            ).isoformat()
            connection.execute(
                """UPDATE runtime_jobs SET status=?, error=?, available_at=?,
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE job_id=?""",
                (status, error[:2000], available, _now(), grant.job.job_id),
            )
            self._record_metric(
                connection, str(row["tenant_id"]),
                "job_retry" if retry else "job_dead", 1,
                {"pool": str(row["pool"]), "job_type": str(row["job_type"])},
            )
            updated = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (grant.job.job_id,)
            ).fetchone()
            connection.commit()
        return _job(updated)

    def get_job(self, job_id: str, *, tenant_id: str | None = None) -> RuntimeJob:
        query = "SELECT * FROM runtime_jobs WHERE job_id=?"
        params: tuple[Any, ...] = (job_id,)
        if tenant_id:
            query += " AND tenant_id=?"
            params += (tenant_id,)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise KeyError("Runtime job not found")
        return _job(row)

    def list_jobs(
        self,
        *,
        tenant_id: str,
        job_type: str | None = None,
        idempotency_prefix: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeJob]:
        """Return tenant-scoped durable receipts in newest-first order."""

        query = "SELECT * FROM runtime_jobs WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if job_type is not None:
            query += " AND job_type=?"
            params.append(job_type)
        if idempotency_prefix is not None:
            query += " AND idempotency_key LIKE ? ESCAPE '\\'"
            escaped = (
                idempotency_prefix.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            params.append(f"{escaped}%")
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(500, limit)))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_job(row) for row in rows]

    def prepare_execution(
        self,
        *,
        tenant_id: str,
        resource_id: str,
        operation: str,
        plan: dict[str, Any],
        owner_id: str,
        lease_seconds: int | None = None,
    ) -> ExecutionPermit:
        plan_hash = _fingerprint(plan)
        idempotency_key = _fingerprint(
            {
                "tenant_id": tenant_id,
                "resource_id": resource_id,
                "operation": operation,
                "plan_hash": plan_hash,
            }
        )
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds or self.lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            effect = connection.execute(
                "SELECT * FROM business_effects WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if effect is not None:
                connection.commit()
                stored = _effect(effect)
                return ExecutionPermit(
                    saga_id=f"replay_{stored.effect_id}",
                    tenant_id=tenant_id,
                    resource_id=resource_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    plan_hash=plan_hash,
                    owner_id=owner_id,
                    fencing_token=stored.fencing_token,
                    expected_version=max(0, stored.resource_version - 1),
                    lease_expires_at=now,
                    replay_result=stored.result,
                )
            guard = connection.execute(
                "SELECT * FROM resource_guards WHERE tenant_id=? AND resource_id=?",
                (tenant_id, resource_id),
            ).fetchone()
            if guard is None:
                connection.execute(
                    """INSERT INTO resource_guards(
                        tenant_id, resource_id, resource_version, fence_counter, updated_at
                    ) VALUES(?, ?, 0, 0, ?)""",
                    (tenant_id, resource_id, now),
                )
                guard = connection.execute(
                    "SELECT * FROM resource_guards WHERE tenant_id=? AND resource_id=?",
                    (tenant_id, resource_id),
                ).fetchone()
            active_expiry = _parse_optional(guard["lease_expires_at"])
            if (
                guard["lease_owner"]
                and active_expiry is not None
                and active_expiry > now_dt
                and guard["lease_owner"] != owner_id
            ):
                raise ResourceBusyError(
                    f"Resource '{resource_id}' is being changed by another worker"
                )
            if (
                guard["lease_owner"]
                and active_expiry is not None
                and active_expiry <= now_dt
            ):
                connection.execute(
                    """UPDATE execution_sagas SET status='needs_attention',
                        error='resource_lease_expired_and_superseded', updated_at=?
                    WHERE tenant_id=? AND resource_id=?
                    AND fencing_token=? AND status IN ('prepared','executing')""",
                    (now, tenant_id, resource_id, int(guard["active_token"] or 0)),
                )
            token = int(guard["fence_counter"]) + 1
            expected_version = int(guard["resource_version"])
            connection.execute(
                """UPDATE resource_guards SET fence_counter=?, active_token=?,
                    lease_owner=?, lease_expires_at=?, updated_at=?
                WHERE tenant_id=? AND resource_id=?""",
                (token, token, owner_id, expires, now, tenant_id, resource_id),
            )
            saga_id = f"saga_{uuid4().hex[:16]}"
            existing_saga = connection.execute(
                "SELECT saga_id FROM execution_sagas WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing_saga:
                saga_id = str(existing_saga["saga_id"])
                connection.execute(
                    """UPDATE execution_sagas SET status='prepared', fencing_token=?,
                        expected_version=?, error=NULL, updated_at=? WHERE saga_id=?""",
                    (token, expected_version, now, saga_id),
                )
            else:
                connection.execute(
                    """INSERT INTO execution_sagas(
                        saga_id, tenant_id, resource_id, operation, idempotency_key,
                        plan_hash, status, fencing_token, expected_version,
                        created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 'prepared', ?, ?, ?, ?)""",
                    (
                        saga_id,
                        tenant_id,
                        resource_id,
                        operation,
                        idempotency_key,
                        plan_hash,
                        token,
                        expected_version,
                        now,
                        now,
                    ),
                )
            self._insert_outbox(
                connection,
                tenant_id=tenant_id,
                aggregate_id=saga_id,
                event_type="execution_prepared",
                event_key=f"{idempotency_key}:prepared:{token}",
                payload={"resource_id": resource_id, "operation": operation, "token": token},
                now=now,
            )
            connection.commit()
        return ExecutionPermit(
            saga_id=saga_id,
            tenant_id=tenant_id,
            resource_id=resource_id,
            operation=operation,
            idempotency_key=idempotency_key,
            plan_hash=plan_hash,
            owner_id=owner_id,
            fencing_token=token,
            expected_version=expected_version,
            lease_expires_at=expires,
        )

    def mark_execution_started(self, permit: ExecutionPermit) -> None:
        if permit.replay_result is not None:
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._validate_fence(connection, permit)
            connection.execute(
                "UPDATE execution_sagas SET status='executing', updated_at=? WHERE saga_id=?",
                (_now(), permit.saga_id),
            )
            connection.commit()

    def confirm_execution(
        self, permit: ExecutionPermit, result: dict[str, Any]
    ) -> tuple[BusinessEffect, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM business_effects WHERE tenant_id=? AND idempotency_key=?",
                (permit.tenant_id, permit.idempotency_key),
            ).fetchone()
            if existing is not None:
                self._record_metric(
                    connection, permit.tenant_id, "business_effect_replay", 1,
                    {"operation": permit.operation},
                )
                connection.commit()
                return _effect(existing), True
            guard = self._validate_fence(connection, permit)
            current_version = int(guard["resource_version"])
            if current_version != permit.expected_version:
                raise OptimisticVersionConflict(
                    f"Expected resource version {permit.expected_version}, got {current_version}"
                )
            new_version = current_version + 1
            effect_id = f"effect_{uuid4().hex[:16]}"
            now = _now()
            connection.execute(
                """INSERT INTO business_effects(
                    effect_id, tenant_id, resource_id, operation, idempotency_key,
                    plan_hash, resource_version, fencing_token, result, confirmed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    effect_id,
                    permit.tenant_id,
                    permit.resource_id,
                    permit.operation,
                    permit.idempotency_key,
                    permit.plan_hash,
                    new_version,
                    permit.fencing_token,
                    _json(result),
                    now,
                ),
            )
            connection.execute(
                """UPDATE resource_guards SET resource_version=?, lease_owner=NULL,
                    lease_expires_at=NULL, updated_at=?
                WHERE tenant_id=? AND resource_id=? AND active_token=?""",
                (
                    new_version,
                    now,
                    permit.tenant_id,
                    permit.resource_id,
                    permit.fencing_token,
                ),
            )
            connection.execute(
                """UPDATE execution_sagas SET status='completed', error=NULL, updated_at=?
                WHERE saga_id=?""",
                (now, permit.saga_id),
            )
            self._insert_outbox(
                connection,
                tenant_id=permit.tenant_id,
                aggregate_id=permit.saga_id,
                event_type="business_effect_confirmed",
                event_key=f"{permit.idempotency_key}:confirmed",
                payload={
                    "effect_id": effect_id,
                    "resource_id": permit.resource_id,
                    "resource_version": new_version,
                },
                now=now,
            )
            row = connection.execute(
                "SELECT * FROM business_effects WHERE effect_id=?", (effect_id,)
            ).fetchone()
            self._record_metric(
                connection, permit.tenant_id, "business_effect_confirmed", 1,
                {"operation": permit.operation},
            )
            connection.commit()
        return _effect(row), False

    def fail_execution(
        self,
        permit: ExecutionPermit,
        error: str,
        *,
        uncertain_external_effect: bool = False,
    ) -> None:
        if permit.replay_result is not None:
            return
        status = "needs_attention" if uncertain_external_effect else "failed"
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE execution_sagas SET status=?, error=?, updated_at=?
                WHERE saga_id=? AND status!='completed'""",
                (status, error[:2000], now, permit.saga_id),
            )
            connection.execute(
                """UPDATE resource_guards SET lease_owner=NULL, lease_expires_at=NULL,
                    updated_at=? WHERE tenant_id=? AND resource_id=?
                    AND active_token=? AND lease_owner=?""",
                (
                    now,
                    permit.tenant_id,
                    permit.resource_id,
                    permit.fencing_token,
                    permit.owner_id,
                ),
            )
            self._insert_outbox(
                connection,
                tenant_id=permit.tenant_id,
                aggregate_id=permit.saga_id,
                event_type="execution_needs_attention" if uncertain_external_effect else "execution_failed",
                event_key=f"{permit.idempotency_key}:{status}:{permit.fencing_token}",
                payload={"error": error[:500], "fencing_token": permit.fencing_token},
                now=now,
            )
            connection.commit()

    def get_effect(
        self, *, tenant_id: str, idempotency_key: str
    ) -> BusinessEffect | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM business_effects WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
        return _effect(row) if row else None

    def lease_outbox(
        self, *, publisher_id: str, lease_seconds: int | None = None
    ) -> dict[str, Any] | None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires = (now_dt + timedelta(seconds=lease_seconds or self.lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE transactional_outbox SET status='pending', lease_owner=NULL,
                    lease_expires_at=NULL WHERE status='publishing' AND lease_expires_at<=?""",
                (now,),
            )
            row = connection.execute(
                """SELECT * FROM transactional_outbox WHERE status='pending'
                ORDER BY created_at LIMIT 1"""
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            token = int(row["lease_token"]) + 1
            connection.execute(
                """UPDATE transactional_outbox SET status='publishing', lease_owner=?,
                    lease_token=?, lease_expires_at=? WHERE outbox_id=? AND status='pending'""",
                (publisher_id, token, expires, row["outbox_id"]),
            )
            leased = connection.execute(
                "SELECT * FROM transactional_outbox WHERE outbox_id=?", (row["outbox_id"],)
            ).fetchone()
            connection.commit()
        return {
            **dict(leased),
            "payload": json.loads(leased["payload"]),
        }

    def mark_outbox_published(
        self, *, outbox_id: str, publisher_id: str, lease_token: int
    ) -> None:
        now = _now()
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE transactional_outbox SET status='published', published_at=?,
                    lease_owner=NULL, lease_expires_at=NULL
                WHERE outbox_id=? AND status='publishing' AND lease_owner=?
                AND lease_token=? AND lease_expires_at>?""",
                (now, outbox_id, publisher_id, lease_token, now),
            ).rowcount
        if changed != 1:
            raise StaleLeaseError("Publisher no longer owns this Outbox event")

    def sagas(self, *, tenant_id: str | None = None, limit: int = 100) -> list[SagaRecord]:
        query = "SELECT * FROM execution_sagas"
        params: tuple[Any, ...] = ()
        if tenant_id:
            query += " WHERE tenant_id=?"
            params = (tenant_id,)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params += (max(1, min(limit, 500)),)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_saga(row) for row in rows]

    def snapshot(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        tenant_clause = " WHERE tenant_id=?" if tenant_id else ""
        params: tuple[Any, ...] = (tenant_id,) if tenant_id else ()
        with self._connect() as connection:
            queue_rows = connection.execute(
                f"SELECT pool, status, COUNT(*) AS count FROM runtime_jobs{tenant_clause} GROUP BY pool, status",
                params,
            ).fetchall()
            saga_rows = connection.execute(
                f"SELECT status, COUNT(*) AS count FROM execution_sagas{tenant_clause} GROUP BY status",
                params,
            ).fetchall()
            effect_count = connection.execute(
                f"SELECT COUNT(*) FROM business_effects{tenant_clause}", params
            ).fetchone()[0]
            pending_outbox = connection.execute(
                f"SELECT COUNT(*) FROM transactional_outbox{tenant_clause}"
                + (" AND status='pending'" if tenant_id else " WHERE status='pending'"),
                params,
            ).fetchone()[0]
            active_leases = connection.execute(
                f"SELECT COUNT(*) FROM runtime_jobs{tenant_clause}"
                + (" AND status='leased'" if tenant_id else " WHERE status='leased'"),
                params,
            ).fetchone()[0]
            metric_rows = connection.execute(
                f"SELECT metric_name, COUNT(*) AS samples, AVG(value) AS average, "
                f"MAX(value) AS maximum FROM runtime_metric_events{tenant_clause} "
                "GROUP BY metric_name",
                params,
            ).fetchall()
        return {
            "protocol_version": "1.1",
            "storage": "sqlite_wal_multi_worker_reference",
            "tenant_id": tenant_id,
            "queue": [dict(row) for row in queue_rows],
            "active_job_leases": int(active_leases),
            "sagas": {str(row["status"]): int(row["count"]) for row in saga_rows},
            "confirmed_business_effects": int(effect_count),
            "pending_outbox_events": int(pending_outbox),
            "bulkheads": self.bulkheads.snapshot(),
            "metrics": [dict(row) for row in metric_rows],
            "limits": {
                "global_queue_depth": self.global_queue_limit,
                "tenant_queue_depth": self.tenant_queue_limit,
                "tenant_requests_per_minute": self.tenant_rate_per_minute,
                "lease_seconds": self.lease_seconds,
            },
        }

    def reset_tenant(self, tenant_id: str) -> None:
        """Reset demo data for one tenant without exposing or touching another tenant."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "runtime_jobs",
                "tenant_dispatch_state",
                "tenant_rate_windows",
                "business_effects",
                "transactional_outbox",
                "execution_sagas",
                "resource_guards",
                "runtime_metric_events",
            ):
                connection.execute(f"DELETE FROM {table} WHERE tenant_id=?", (tenant_id,))
            connection.commit()

    def run_once(
        self,
        *,
        worker_id: str,
        pool: PoolName,
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    ) -> RuntimeJob | None:
        grant = self.lease_next(worker_id=worker_id, pool=pool)
        if grant is None:
            return None
        handler = handlers.get(grant.job.job_type)
        if handler is None:
            return self.fail(grant, f"No handler registered for {grant.job.job_type}")
        try:
            with self.bulkheads.acquire(pool):
                result = handler(grant.job.payload)
            return self.complete(grant, result)
        except Exception as exc:
            return self.fail(grant, str(exc) or type(exc).__name__)

    def _finish_job(
        self,
        grant: LeaseGrant,
        *,
        status: str,
        result: dict[str, Any],
    ) -> RuntimeJob:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_job(connection, grant)
            connection.execute(
                """UPDATE runtime_jobs SET status=?, result=?, error=NULL,
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE job_id=?""",
                (status, _json(result), _now(), grant.job.job_id),
            )
            elapsed_ms = max(
                0.0,
                (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(str(grant.job.updated_at))
                ).total_seconds()
                * 1000,
            )
            self._record_metric(
                connection, grant.job.tenant_id, "job_execution_ms", elapsed_ms,
                {"pool": grant.job.pool, "job_type": grant.job.job_type},
            )
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE job_id=?", (grant.job.job_id,)
            ).fetchone()
            connection.commit()
        return _job(row)

    def metric_events(
        self, *, tenant_id: str, metric_name: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM runtime_metric_events WHERE tenant_id=?"
        params: tuple[Any, ...] = (tenant_id,)
        if metric_name:
            query += " AND metric_name=?"
            params += (metric_name,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params += (max(1, min(limit, 10_000)),)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                **dict(row),
                "dimensions": json.loads(row["dimensions"]),
            }
            for row in rows
        ]

    @staticmethod
    def _record_metric(
        connection: sqlite3.Connection,
        tenant_id: str,
        metric_name: str,
        value: float,
        dimensions: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO runtime_metric_events(
                metric_id, tenant_id, metric_name, value, dimensions, created_at
            ) VALUES(?,?,?,?,?,?)""",
            (
                f"metric_{uuid4().hex[:16]}", tenant_id, metric_name, float(value),
                _json(dimensions), _now(),
            ),
        )

    def _owned_job(self, connection: sqlite3.Connection, grant: LeaseGrant) -> sqlite3.Row:
        row = connection.execute(
            """SELECT * FROM runtime_jobs WHERE job_id=? AND status='leased'
            AND lease_owner=? AND lease_token=?""",
            (grant.job.job_id, grant.worker_id, grant.lease_token),
        ).fetchone()
        if row is None:
            raise StaleLeaseError("Worker no longer owns this job lease")
        expiry = _parse_optional(row["lease_expires_at"])
        if expiry is None or expiry <= datetime.now(timezone.utc):
            raise StaleLeaseError("Job lease expired before commit")
        return row

    def _validate_fence(
        self, connection: sqlite3.Connection, permit: ExecutionPermit
    ) -> sqlite3.Row:
        guard = connection.execute(
            "SELECT * FROM resource_guards WHERE tenant_id=? AND resource_id=?",
            (permit.tenant_id, permit.resource_id),
        ).fetchone()
        if (
            guard is None
            or int(guard["active_token"] or -1) != permit.fencing_token
            or guard["lease_owner"] != permit.owner_id
        ):
            raise StaleFencingTokenError("Execution permit has been superseded")
        expiry = _parse_optional(guard["lease_expires_at"])
        if expiry is None or expiry <= datetime.now(timezone.utc):
            raise StaleFencingTokenError("Execution permit expired before commit")
        return guard

    def _check_backpressure(self, connection: sqlite3.Connection, tenant_id: str) -> None:
        active = "('queued','leased')"
        global_depth = connection.execute(
            f"SELECT COUNT(*) FROM runtime_jobs WHERE status IN {active}"
        ).fetchone()[0]
        tenant_depth = connection.execute(
            f"SELECT COUNT(*) FROM runtime_jobs WHERE tenant_id=? AND status IN {active}",
            (tenant_id,),
        ).fetchone()[0]
        if global_depth >= self.global_queue_limit:
            raise QueueBackpressureError("Global runtime queue is at capacity")
        if tenant_depth >= self.tenant_queue_limit:
            raise QueueBackpressureError("Tenant runtime queue is at capacity")

    def _consume_rate_slot(
        self, connection: sqlite3.Connection, tenant_id: str, now: str
    ) -> None:
        row = connection.execute(
            "SELECT * FROM tenant_rate_windows WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        now_dt = datetime.fromisoformat(now)
        if row is None or now_dt - datetime.fromisoformat(row["window_started_at"]) >= timedelta(minutes=1):
            connection.execute(
                """INSERT INTO tenant_rate_windows(tenant_id, window_started_at, request_count)
                VALUES(?, ?, 1) ON CONFLICT(tenant_id) DO UPDATE SET
                window_started_at=excluded.window_started_at, request_count=1""",
                (tenant_id, now),
            )
            return
        if int(row["request_count"]) >= self.tenant_rate_per_minute:
            raise TenantRateLimitError("Tenant request rate exceeded; retry after the window resets")
        connection.execute(
            "UPDATE tenant_rate_windows SET request_count=request_count+1 WHERE tenant_id=?",
            (tenant_id,),
        )

    def _reclaim_expired(self, connection: sqlite3.Connection, now: str) -> None:
        connection.execute(
            """UPDATE runtime_jobs SET
                status=CASE WHEN attempts>=max_attempts THEN 'dead' ELSE 'queued' END,
                error=CASE WHEN attempts>=max_attempts THEN 'lease_expired_max_attempts' ELSE error END,
                lease_owner=NULL, lease_expires_at=NULL, available_at=?, updated_at=?
            WHERE status='leased' AND lease_expires_at<=?""",
            (now, now, now),
        )

    @staticmethod
    def _insert_outbox(
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        aggregate_id: str,
        event_type: str,
        event_key: str,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO transactional_outbox(
                outbox_id, tenant_id, aggregate_id, event_type, event_key,
                payload, status, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                f"outbox_{uuid4().hex[:16]}",
                tenant_id,
                aggregate_id,
                event_type,
                event_key,
                _json(payload),
                now,
            ),
        )


def _job(row: sqlite3.Row) -> RuntimeJob:
    return RuntimeJob(
        job_id=row["job_id"],
        tenant_id=row["tenant_id"],
        pool=row["pool"],
        job_type=row["job_type"],
        idempotency_key=row["idempotency_key"],
        payload_fingerprint=row["payload_fingerprint"],
        payload=json.loads(row["payload"]),
        status=row["status"],
        priority=row["priority"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_token=row["lease_token"],
        lease_expires_at=row["lease_expires_at"],
        available_at=row["available_at"],
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _effect(row: sqlite3.Row) -> BusinessEffect:
    return BusinessEffect(
        effect_id=row["effect_id"],
        tenant_id=row["tenant_id"],
        resource_id=row["resource_id"],
        operation=row["operation"],
        idempotency_key=row["idempotency_key"],
        plan_hash=row["plan_hash"],
        resource_version=row["resource_version"],
        fencing_token=row["fencing_token"],
        result=json.loads(row["result"]),
        confirmed_at=row["confirmed_at"],
    )


def _saga(row: sqlite3.Row) -> SagaRecord:
    return SagaRecord(
        saga_id=row["saga_id"],
        tenant_id=row["tenant_id"],
        resource_id=row["resource_id"],
        operation=row["operation"],
        idempotency_key=row["idempotency_key"],
        plan_hash=row["plan_hash"],
        status=row["status"],
        fencing_token=row["fencing_token"],
        expected_version=row["expected_version"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_optional(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
