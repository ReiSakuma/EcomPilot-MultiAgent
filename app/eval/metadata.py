from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import (
    INTERVIEW_DATASET_VERSION,
    PROJECT_ROOT,
    PROJECT_VERSION,
    PROMPT_VERSION,
)


def build_run_metadata(
    *,
    dataset_path: Path | None = None,
    profile: str,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a credential-free snapshot that makes an eval report reproducible."""
    metadata: dict[str, Any] = {
        "project_version": PROJECT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "dataset_version": INTERVIEW_DATASET_VERSION,
        "profile": profile,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
    if dataset_path is not None:
        resolved = dataset_path.resolve()
        metadata["dataset_path"] = _relative_path(resolved)
        metadata["dataset_sha256"] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if runtime is not None:
        metadata["runtime"] = sanitize_runtime(runtime)
    return metadata


def sanitize_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Keep useful runtime facts while making secret inclusion impossible by shape."""
    allowed = {
        "provider",
        "model",
        "base_url",
        "api_key_configured",
        "enabled_agents",
        "fallback_mode",
        "timeout_seconds",
        "max_retries",
        "max_output_tokens",
        "llm_request_budget_seconds",
        "node_timeout_seconds",
        "max_calls_per_agent",
        "max_repair_attempts",
        "real_llm_enabled",
        "backend",
        "headless",
        "timeout_ms",
        "playwright_package_installed",
        "chromium_installed",
        "real_browser_enabled",
        "ready",
        "issues",
    }
    return {key: runtime[key] for key in sorted(allowed & runtime.keys())}


def write_json_report(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return path.name
