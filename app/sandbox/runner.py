from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from app.config import (
    PROJECT_ROOT,
    SQL_SANDBOX_MAX_OUTPUT_BYTES,
    SQL_SANDBOX_MEMORY_MB,
    SQL_SANDBOX_QUERY_TIMEOUT_SECONDS,
    SQL_SANDBOX_TIMEOUT_SECONDS,
)
from app.sandbox.schemas import (
    SandboxIsolation,
    SandboxLimits,
    SandboxReceipt,
    SqlSandboxResult,
)

if TYPE_CHECKING:
    from app.sql.policy import SqlPolicyDecision


SANDBOX_PROTOCOL_VERSION = "1.0"
SANDBOX_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
}


class SandboxExecutionError(RuntimeError):
    safe_to_retry = False

    def __init__(self, message: str, receipt: SandboxReceipt | None = None) -> None:
        super().__init__(message)
        self.receipt = receipt


class SandboxTimeoutError(SandboxExecutionError, TimeoutError):
    pass


class SandboxProtocolError(SandboxExecutionError):
    pass


class SqlSandboxRunner:
    """Executes a pre-authorized SELECT in a fixed, isolated stdlib worker."""

    def __init__(
        self,
        *,
        timeout_seconds: float = SQL_SANDBOX_TIMEOUT_SECONDS,
        query_timeout_seconds: float = SQL_SANDBOX_QUERY_TIMEOUT_SECONDS,
        memory_mb: int = SQL_SANDBOX_MEMORY_MB,
        max_output_bytes: int = SQL_SANDBOX_MAX_OUTPUT_BYTES,
        worker_path: Path | None = None,
    ) -> None:
        timeout_seconds = max(0.1, min(float(timeout_seconds), 10.0))
        query_timeout_seconds = max(
            0.05, min(float(query_timeout_seconds), timeout_seconds, 5.0)
        )
        self.limits = SandboxLimits(
            wall_time_seconds=timeout_seconds,
            query_time_seconds=query_timeout_seconds,
            cpu_seconds=max(1, min(10, math.ceil(timeout_seconds))),
            memory_mb=max(64, min(int(memory_mb), 1024)),
            max_open_files=16,
            max_processes=1,
            max_file_bytes=0,
            max_request_bytes=16_384,
            max_output_bytes=max(16_384, min(int(max_output_bytes), 5_000_000)),
        )
        self.worker_path = worker_path or (
            PROJECT_ROOT / "app" / "sandbox" / "sql_worker.py"
        )

    def execute(
        self,
        decision: SqlPolicyDecision,
        *,
        database_path: Path,
        dataset_version: str,
    ) -> SqlSandboxResult:
        if decision.status != "allowed" or not decision.normalized_sql:
            raise SandboxExecutionError(
                "Sandbox execution requires an allowed SQL policy decision"
            )
        sandbox_id = f"sandbox_{uuid4().hex[:12]}"
        request = {
            "protocol_version": SANDBOX_PROTOCOL_VERSION,
            "sandbox_id": sandbox_id,
            "database_path": str(database_path.resolve(strict=True)),
            "normalized_sql": decision.normalized_sql,
            "row_limit": decision.enforced_limit,
            "query_timeout_seconds": self.limits.query_time_seconds,
            "dataset_version": dataset_version,
            "limits": self.limits.model_dump(mode="json"),
        }
        encoded_request = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded_request) > self.limits.max_request_bytes:
            raise SandboxProtocolError("Sandbox request exceeds parent IPC limit")

        command = [sys.executable, "-I", "-S", str(self.worker_path)]
        started = perf_counter()
        with tempfile.TemporaryDirectory(prefix="ecompilot-sql-") as working_directory:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=working_directory,
                env=dict(SANDBOX_ENVIRONMENT),
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(
                    input=encoded_request,
                    timeout=self.limits.wall_time_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                _kill_process_group(process)
                stdout, stderr = process.communicate()
                receipt = self._receipt(
                    sandbox_id=sandbox_id,
                    status="timed_out",
                    started=started,
                    request_bytes=len(encoded_request),
                    response_bytes=len(stdout),
                    worker_pid=process.pid,
                    exit_code=process.returncode,
                    error_type="SandboxTimeoutError",
                    error="SQL sandbox exceeded wall-time budget",
                )
                raise SandboxTimeoutError(receipt.error or "Sandbox timed out", receipt) from exc

        if len(stdout) > self.limits.max_output_bytes:
            receipt = self._receipt(
                sandbox_id=sandbox_id,
                status="failed",
                started=started,
                request_bytes=len(encoded_request),
                response_bytes=len(stdout),
                worker_pid=process.pid,
                exit_code=process.returncode,
                error_type="SandboxProtocolError",
                error="SQL sandbox response exceeds IPC output limit",
            )
            raise SandboxProtocolError(receipt.error or "Oversized sandbox response", receipt)
        payload = self._parse_response(
            stdout,
            stderr,
            process.returncode,
            process.pid,
            sandbox_id,
            started,
            len(encoded_request),
        )
        worker = payload["worker"]
        receipt = self._receipt(
            sandbox_id=sandbox_id,
            status="completed",
            started=started,
            request_bytes=len(encoded_request),
            response_bytes=len(stdout),
            worker_pid=worker["pid"],
            exit_code=process.returncode,
            environment_keys=tuple(worker["environment_keys"]),
            secret_environment_present=worker["secret_environment_present"],
            resource_limits_applied=tuple(worker["resource_limits_applied"]),
            working_directory_removed=not Path(worker["cwd"]).exists(),
        )
        try:
            return SqlSandboxResult(**payload["result"], sandbox=receipt)
        except ValidationError as exc:
            raise SandboxProtocolError("Sandbox returned an invalid result contract", receipt) from exc

    def status(self) -> dict[str, Any]:
        return {
            "backend": "subprocess",
            "protocol_version": SANDBOX_PROTOCOL_VERSION,
            "worker": str(self.worker_path),
            "worker_exists": self.worker_path.is_file(),
            "command_mode": "fixed_argv_no_shell",
            "limits": self.limits.model_dump(mode="json"),
            "environment_allowlist": sorted(SANDBOX_ENVIRONMENT),
            "isolation": {
                "separate_process": True,
                "isolated_python": True,
                "site_packages_disabled": True,
                "temporary_working_directory": True,
                "sqlite_read_only": True,
                "sqlite_authorizer": True,
                "namespaces": False,
                "seccomp": False,
                "container": False,
            },
        }

    def _parse_response(
        self,
        stdout: bytes,
        stderr: bytes,
        exit_code: int | None,
        process_pid: int,
        sandbox_id: str,
        started: float,
        request_bytes: int,
    ) -> dict[str, Any]:
        def protocol_error(message: str) -> SandboxProtocolError:
            receipt = self._receipt(
                sandbox_id=sandbox_id,
                status="failed",
                started=started,
                request_bytes=request_bytes,
                response_bytes=len(stdout),
                worker_pid=process_pid,
                exit_code=exit_code,
                error_type="SandboxProtocolError",
                error=message,
            )
            return SandboxProtocolError(message, receipt)

        try:
            payload = json.loads(stdout)
        except Exception as exc:
            raise protocol_error("SQL sandbox returned malformed JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") not in {"completed", "failed"}:
            raise protocol_error("SQL sandbox returned an invalid envelope")
        if exit_code != 0 or payload["status"] == "failed":
            error = str(payload.get("error") or stderr.decode("utf-8", "replace") or "worker failed")[:1000]
            receipt = self._receipt(
                sandbox_id=sandbox_id,
                status="failed",
                started=started,
                request_bytes=request_bytes,
                response_bytes=len(stdout),
                worker_pid=process_pid,
                exit_code=exit_code,
                error_type=str(payload.get("error_type") or "SandboxWorkerError"),
                error=error,
            )
            raise SandboxExecutionError(error, receipt)
        if set(payload) != {"status", "result", "worker"}:
            raise protocol_error("SQL sandbox returned unexpected envelope fields")
        worker = payload["worker"]
        if not isinstance(worker, dict) or set(worker) != {
            "pid",
            "environment_keys",
            "secret_environment_present",
            "resource_limits_applied",
            "cwd",
        }:
            raise protocol_error("SQL sandbox returned invalid worker evidence")
        if not isinstance(worker["pid"], int) or worker["pid"] != process_pid:
            raise protocol_error("SQL sandbox worker PID evidence mismatch")
        return payload

    def _receipt(
        self,
        *,
        sandbox_id: str,
        status: str,
        started: float,
        request_bytes: int,
        response_bytes: int,
        worker_pid: int | None = None,
        exit_code: int | None = None,
        environment_keys: tuple[str, ...] = tuple(SANDBOX_ENVIRONMENT),
        secret_environment_present: bool = False,
        resource_limits_applied: tuple[str, ...] = (),
        working_directory_removed: bool = True,
        error_type: str | None = None,
        error: str | None = None,
    ) -> SandboxReceipt:
        return SandboxReceipt(
            sandbox_id=sandbox_id,
            status=status,
            worker_pid=worker_pid,
            exit_code=exit_code,
            duration_ms=round((perf_counter() - started) * 1000, 3),
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            limits=self.limits,
            isolation=SandboxIsolation(
                separate_process=True,
                isolated_python=True,
                site_packages_disabled=True,
                shell_enabled=False,
                temporary_working_directory=True,
                working_directory_removed=working_directory_removed,
                environment_allowlist=tuple(sorted(SANDBOX_ENVIRONMENT)),
                environment_key_count=len(environment_keys),
                secret_environment_present=secret_environment_present,
                sqlite_read_only=True,
                sqlite_authorizer=True,
                resource_limits_applied=resource_limits_applied,
            ),
            error_type=error_type,
            error=error,
        )


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
