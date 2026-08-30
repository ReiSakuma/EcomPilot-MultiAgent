from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


BASE_URL = os.getenv("ECOMPILOT_BROWSER_BASE_URL", "http://127.0.0.1:8131").rstrip("/")
GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def main() -> None:
    runtime = _request("GET", "/browser/status")
    if not runtime.get("ready") or not runtime.get("real_browser_enabled"):
        print(json.dumps(runtime, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    _request("POST", "/seller-center/reset")
    state = _request(
        "POST",
        "/tasks/run",
        {
            "goal": GOAL,
            "approval": {
                "approved": True,
                "approver": "real-browser-smoke",
                "reason": "v15 Playwright smoke",
            },
        },
    )
    browser = state.get("agent_outputs", {}).get("browser_agent", {})
    result = {
        "status": state.get("status"),
        "task_id": state.get("task_id"),
        "run_id": state.get("run_id"),
        "runtime": runtime,
        "browser_result": browser.get("browser_result"),
        "verification": browser.get("verification"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if (
        state.get("status") != "completed"
        or browser.get("browser_result", {}).get("backend") != "playwright"
        or not browser.get("verification", {}).get("verified")
    ):
        raise SystemExit(1)


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"Cannot reach V15 server at {BASE_URL}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
