from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8215").rstrip("/")
TASK_ID = os.getenv("ECOMPILOT_VISUAL_TASK_ID", "task_5c772b8e")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v32"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1536, "height": 960})
        page.goto(f"{BASE_URL}/ops?task_id={TASK_ID}&pin=1", wait_until="networkidle")
        page.get_by_role("button", name="Routing").click()
        page.wait_for_function(
            "document.querySelector('#routingGrid').textContent.includes('modify_listing_workflow.v1')"
        )
        routing_text = page.locator("#routingGrid").inner_text()
        ops_screenshot = OUTPUT_DIR / "routing_operations.png"
        page.screenshot(path=str(ops_screenshot), full_page=True)

        user = browser.new_page(viewport={"width": 1440, "height": 900})
        user.goto(BASE_URL, wait_until="networkidle")
        user_screenshot = OUTPUT_DIR / "conversation_desktop.png"
        user.screenshot(path=str(user_screenshot), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(BASE_URL, wait_until="networkidle")
        mobile_screenshot = OUTPUT_DIR / "conversation_mobile.png"
        mobile.screenshot(path=str(mobile_screenshot), full_page=True)
        result = {
            "status": "passed",
            "routing_template_visible": "modify_listing_workflow.v1" in routing_text,
            "skipped_agents_visible": "明确跳过的 Agent" in routing_text,
            "capability_context_visible": "write_execute" in routing_text,
            "operations_horizontal_overflow": page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            ),
            "user_desktop_horizontal_overflow": user.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            ),
            "user_mobile_horizontal_overflow": mobile.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth"
            ),
            "screenshots": [str(ops_screenshot), str(user_screenshot), str(mobile_screenshot)],
        }
        browser.close()
    if any(
        result[key]
        for key in (
            "operations_horizontal_overflow",
            "user_desktop_horizontal_overflow",
            "user_mobile_horizontal_overflow",
        )
    ) or not all(
        result[key]
        for key in (
            "routing_template_visible",
            "skipped_agents_visible",
            "capability_context_visible",
        )
    ):
        result["status"] = "failed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
