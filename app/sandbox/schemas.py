from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    wall_time_seconds: float = Field(gt=0, le=10)
    query_time_seconds: float = Field(gt=0, le=5)
    cpu_seconds: int = Field(ge=1, le=10)
    memory_mb: int = Field(ge=32, le=1024)
    max_open_files: int = Field(ge=8, le=128)
    max_processes: int = Field(ge=1, le=16)
    max_file_bytes: int = Field(ge=0, le=10_000_000)
    max_request_bytes: int = Field(ge=1024, le=1_000_000)
    max_output_bytes: int = Field(ge=4096, le=5_000_000)


class SandboxIsolation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    separate_process: bool
    isolated_python: bool
    site_packages_disabled: bool
    shell_enabled: bool
    temporary_working_directory: bool
    working_directory_removed: bool
    environment_allowlist: tuple[str, ...]
    environment_key_count: int
    secret_environment_present: bool
    sqlite_read_only: bool
    sqlite_authorizer: bool
    resource_limits_applied: tuple[str, ...]


class SandboxReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sandbox_id: str
    backend: Literal["subprocess"] = "subprocess"
    status: Literal["completed", "failed", "timed_out"]
    worker_pid: int | None = None
    exit_code: int | None = None
    duration_ms: float
    request_bytes: int
    response_bytes: int
    limits: SandboxLimits
    isolation: SandboxIsolation
    error_type: str | None = None
    error: str | None = None


class SqlSandboxResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)
    truncated: bool
    elapsed_ms: float = Field(ge=0)
    dataset_version: str
    sandbox: SandboxReceipt
