from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8234").rstrip("/")
TASK_ID = os.getenv("ECOMPILOT_VISUAL_TASK_ID", "task_49403d48")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v33"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        ops = browser.new_page(viewport={"width": 1536, "height": 960})
        ops.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        ops.goto(f"{BASE_URL}/ops?task_id={TASK_ID}&pin=1", wait_until="networkidle")
        ops.get_by_role("button", name="Memory").click()
        ops.wait_for_function(
            "document.querySelector('#memoryGrid').textContent.includes('Context Policy 2.0')"
        )
        memory_text = ops.locator("#memoryGrid").inner_text()
        ops_screenshot = OUTPUT_DIR / "memory_operations.png"
        ops.screenshot(path=str(ops_screenshot), full_page=True)

        user = browser.new_page(viewport={"width": 1440, "height": 900})
        user.goto(f"{BASE_URL}/user", wait_until="networkidle")
        user_screenshot = OUTPUT_DIR / "conversation_desktop.png"
        user.screenshot(path=str(user_screenshot), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(f"{BASE_URL}/user", wait_until="networkidle")
        mobile_screenshot = OUTPUT_DIR / "conversation_mobile.png"
        mobile.screenshot(path=str(mobile_screenshot), full_page=True)

        memories_response = ops.request.get(f"{BASE_URL}/api/copilot/memories")
        openapi = ops.request.get(f"{BASE_URL}/openapi.json").json()
        result = {
            "status": "passed",
            "memory_context_visible": "Context Policy 2.0" in memory_text,
            "memory_refs_visible": "本任务实际召回" in memory_text,
            "memory_api_ok": memories_response.ok,
            "confirmation_api_documented": any(
                path.endswith("/confirm") for path in openapi.get("paths", {})
            ),
            "console_errors": console_errors,
            "operations_horizontal_overflow": ops.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            ),
            "user_desktop_horizontal_overflow": user.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            ),
            "screenshots": [str(ops_screenshot), str(user_screenshot), str(mobile_screenshot)],
        }
        browser.close()
    required = (
        "memory_context_visible", "memory_refs_visible", "memory_api_ok",
        "confirmation_api_documented",
    )
    if not all(result[key] for key in required) or result["console_errors"]:
        result["status"] = "failed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
