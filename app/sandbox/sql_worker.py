"""Fixed stdlib-only SQL worker. The model never controls this program or its argv."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


MAX_STDIN_BYTES = 65_536
MAX_CELL_CHARS = 500
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION", "CREDENTIAL")


def main() -> int:
    try:
        os.umask(0o077)
        request = _read_request()
        applied_limits = _apply_limits(request["limits"])
        response = _execute(request, applied_limits)
        _write_response({"status": "completed", **response})
        return 0
    except Exception as exc:
        _write_response(
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
        )
        return 1


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise ValueError("sandbox request exceeds worker input limit")
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise ValueError("invalid sandbox JSON request") from exc
    expected = {
        "protocol_version",
        "sandbox_id",
        "database_path",
        "normalized_sql",
        "row_limit",
        "query_timeout_seconds",
        "dataset_version",
        "limits",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("invalid sandbox request fields")
    if payload["protocol_version"] != "1.0":
        raise ValueError("unsupported sandbox protocol version")
    if not isinstance(payload["normalized_sql"], str) or not payload["normalized_sql"]:
        raise ValueError("normalized_sql must be a non-empty string")
    if not isinstance(payload["row_limit"], int) or not 1 <= payload["row_limit"] <= 100:
        raise ValueError("row_limit is outside worker bounds")
    return payload


def _apply_limits(limits: dict[str, Any]) -> list[str]:
    try:
        import resource
    except ImportError:
        return []
    configured = {
        "cpu": (resource.RLIMIT_CPU, int(limits["cpu_seconds"])),
        "address_space": (
            resource.RLIMIT_AS,
            int(limits["memory_mb"]) * 1024 * 1024,
        ),
        "open_files": (resource.RLIMIT_NOFILE, int(limits["max_open_files"])),
        "file_size": (resource.RLIMIT_FSIZE, int(limits["max_file_bytes"])),
    }
    if hasattr(resource, "RLIMIT_NPROC"):
        configured["processes"] = (
            resource.RLIMIT_NPROC,
            int(limits["max_processes"]),
        )
    applied: list[str] = []
    for name, (kind, value) in configured.items():
        try:
            soft, hard = resource.getrlimit(kind)
            effective = min(value, hard) if hard != resource.RLIM_INFINITY else value
            resource.setrlimit(kind, (effective, effective))
            applied.append(name)
        except (OSError, ValueError):
            continue
    return applied


def _execute(request: dict[str, Any], applied_limits: list[str]) -> dict[str, Any]:
    path = Path(request["database_path"])
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("database path must be an existing absolute regular file")
    resolved = path.resolve(strict=True)
    started = time.perf_counter()
    deadline = started + float(request["query_timeout_seconds"])
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True, check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.set_authorizer(_read_only_authorizer)
        connection.set_progress_handler(
            lambda: 1 if time.perf_counter() > deadline else 0, 500
        )
        cursor = connection.execute(request["normalized_sql"])
        raw_rows = cursor.fetchmany(request["row_limit"] + 1)
        rows = [
            {key: _sanitize_cell(row[key]) for key in row.keys()}
            for row in raw_rows[: request["row_limit"]]
        ]
        columns = [description[0] for description in cursor.description or ()]
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"read-only SQL execution failed: {exc}") from exc
    finally:
        connection.close()
    environment_keys = tuple(sorted(os.environ))
    return {
        "result": {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(raw_rows) > request["row_limit"],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "dataset_version": request["dataset_version"],
        },
        "worker": {
            "pid": os.getpid(),
            "environment_keys": environment_keys,
            "secret_environment_present": any(
                marker in key.upper()
                for key in environment_keys
                for marker in SECRET_MARKERS
            ),
            "resource_limits_applied": applied_limits,
            "cwd": os.getcwd(),
        },
    }


def _read_only_authorizer(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def _sanitize_cell(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_CELL_CHARS]
    if value is None or isinstance(value, (int, float)):
        return value
    return str(value)[:MAX_CELL_CHARS]


def _write_response(payload: dict[str, Any]) -> None:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    raise SystemExit(main())
