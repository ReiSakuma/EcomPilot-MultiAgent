from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.config import BROWSER_ARTIFACT_DIR


BASE_URL = os.getenv("ECOMPILOT_BROWSER_BASE_URL", "http://127.0.0.1:8131").rstrip("/")
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 390, "height": 844},
}


def inspect_page(page, name: str) -> dict:
    page.get_by_role("button", name="A2A 协作").click()
    page.wait_for_function(
        "document.querySelectorAll('#capabilityRows tr').length === 5"
    )
    page.wait_for_function(
        "document.querySelectorAll('#delegationRows tr').length >= 5"
    )
    page.wait_for_function(
        "document.querySelectorAll('#artifactLineage .lineage-row').length >= 5"
    )
    layout = page.evaluate(
        """() => ({
          bodyWidth: document.body.scrollWidth,
          viewportWidth: window.innerWidth,
          a2aWidth: document.querySelector('#a2a').getBoundingClientRect().width,
          visible: getComputedStyle(document.querySelector('#a2a')).display !== 'none'
        })"""
    )
    page.evaluate("window.scrollTo(0, 0)")
    screenshot = BROWSER_ARTIFACT_DIR / f"v20_ops_a2a_{name}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    return {
        "viewport": name,
        "capability_rows": page.locator("#capabilityRows tr").count(),
        "delegation_rows": page.locator("#delegationRows tr").count(),
        "artifact_rows": page.locator("#artifactLineage .lineage-row").count(),
        "stats": page.locator("#a2aStats").inner_text(),
        "layout": layout,
        "screenshot": str(screenshot),
        "screenshot_bytes": screenshot.stat().st_size,
    }


def main() -> None:
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            task_response = playwright.request.new_context().post(
                f"{BASE_URL}/tasks/run",
                data={
                    "goal": (
                        "我要上架一款成本95元、售价199元、库存800件的无线耳机，"
                        "面向大学生，毛利率不低于25%。"
                    ),
                    "approval": {"approved": False, "approver": "visual-check"},
                },
            )
            if not task_response.ok:
                raise RuntimeError(f"task setup failed: {task_response.text()}")
            task_id = task_response.json()["task_id"]
            desktop = browser.new_page(viewport=VIEWPORTS["desktop"])
            desktop.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            desktop.goto(
                f"{BASE_URL}/ops?task_id={task_id}&pin=1",
                wait_until="networkidle",
            )
            desktop.wait_for_function(
                "taskId => document.querySelector('#rawJson').textContent.includes(taskId)",
                arg=task_id,
                timeout=10_000,
            )
            linked_url = desktop.url
            results.append(inspect_page(desktop, "desktop"))
            desktop.close()

            mobile = browser.new_page(viewport=VIEWPORTS["mobile"])
            mobile.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            mobile.goto(linked_url, wait_until="networkidle")
            results.append(inspect_page(mobile, "mobile"))
            mobile.close()
        finally:
            browser.close()

    for result in results:
        result["passed"] = (
            result["capability_rows"] == 5
            and result["delegation_rows"] >= 5
            and result["artifact_rows"] >= 5
            and result["layout"]["visible"]
            and result["layout"]["a2aWidth"] > 0
            and result["layout"]["bodyWidth"] <= result["layout"]["viewportWidth"]
            and result["screenshot_bytes"] > 10_000
        )
    report = {
        "version": "v20",
        "passed": all(result["passed"] for result in results) and not console_errors,
        "console_errors": console_errors,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
