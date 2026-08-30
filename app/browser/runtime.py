from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from app.config import (
    BROWSER_BACKEND,
    BROWSER_BASE_URL,
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT_MS,
    BROWSER_TICKET_TTL_SECONDS,
)


def get_browser_runtime_status() -> dict[str, Any]:
    issues: list[str] = []
    package_installed = find_spec("playwright") is not None
    chromium_path: str | None = None
    chromium_installed = False
    if BROWSER_BACKEND not in {"mock", "playwright"}:
        issues.append(f"unsupported_backend:{BROWSER_BACKEND}")
    if BROWSER_BACKEND == "playwright":
        if not package_installed:
            issues.append("playwright_package_missing")
        else:
            browser_root = Path(
                os.getenv("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")
            ).expanduser()
            candidates = sorted(browser_root.glob("chromium-*/chrome-linux*/chrome"))
            if not candidates:
                candidates = sorted(
                    browser_root.glob("chromium_headless_shell-*/chrome-linux*/headless_shell")
                )
            if candidates:
                chromium_path = str(candidates[-1])
                chromium_installed = True
            else:
                issues.append("chromium_missing")
        if not BROWSER_BASE_URL.startswith(("http://", "https://")):
            issues.append("invalid_base_url")
    return {
        "backend": BROWSER_BACKEND,
        "base_url": BROWSER_BASE_URL,
        "headless": BROWSER_HEADLESS,
        "timeout_ms": BROWSER_TIMEOUT_MS,
        "ticket_ttl_seconds": BROWSER_TICKET_TTL_SECONDS,
        "playwright_package_installed": package_installed,
        "chromium_installed": chromium_installed,
        "chromium_path": chromium_path,
        "real_browser_enabled": BROWSER_BACKEND == "playwright",
        "ready": not issues,
        "issues": issues,
    }
