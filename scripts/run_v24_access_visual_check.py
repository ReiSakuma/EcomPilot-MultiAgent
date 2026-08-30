from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.config import BROWSER_ARTIFACT_DIR
from scripts.run_v21_acceptance import run_fixture_task


BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8142").rstrip("/")
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 390, "height": 844},
}


def main() -> None:
    state = run_fixture_task()
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for name, viewport in VIEWPORTS.items():
                page = browser.new_page(viewport=viewport)
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.goto(
                    f"{BASE_URL}/ops?task_id={state.task_id}", wait_until="networkidle"
                )
                page.get_by_role("button", name="Access", exact=True).click()
                page.wait_for_function(
                    "document.querySelector('#accessGrid').textContent.includes('tenant_demo')"
                )
                text = page.locator("#access").inner_text()
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      activeViewWidth: document.querySelector('#access').getBoundingClientRect().width,
                      tableContainerWidth: document.querySelector('#access .table-wrap').clientWidth,
                      tableScrollWidth: document.querySelector('#access .table-wrap').scrollWidth
                    })"""
                )
                page.evaluate("window.scrollTo(0, 0)")
                screenshot = BROWSER_ARTIFACT_DIR / f"v24_tenant_access_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                results.append(
                    {
                        "viewport": name,
                        "access_evidence_visible": all(
                            value in text
                            for value in (
                                "RBAC+ABAC",
                                "tenant_demo",
                                "demo-merchant-a",
                                "SQL 行级过滤",
                                "委派绑定",
                                "工具绑定",
                            )
                        ),
                        "layout": layout,
                        "screenshot": str(screenshot),
                        "screenshot_bytes": screenshot.stat().st_size,
                    }
                )
                page.close()
        finally:
            browser.close()

    for result in results:
        result["passed"] = (
            result["access_evidence_visible"]
            and result["layout"]["bodyWidth"] <= result["layout"]["viewportWidth"]
            and result["layout"]["activeViewWidth"] > 0
            and result["screenshot_bytes"] > 10_000
        )
    report = {
        "version": "v24",
        "passed": all(item["passed"] for item in results) and not console_errors,
        "task_id": state.task_id,
        "console_errors": console_errors,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
