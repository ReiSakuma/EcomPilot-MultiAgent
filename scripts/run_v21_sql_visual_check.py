from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.config import BROWSER_ARTIFACT_DIR
from scripts.run_v21_acceptance import run_fixture_task


BASE_URL = os.getenv("ECOMPILOT_BROWSER_BASE_URL", "http://127.0.0.1:8131").rstrip("/")
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
                    f"{BASE_URL}/ops?task_id={state.task_id}",
                    wait_until="networkidle",
                )
                page.get_by_role("button", name="Market", exact=True).click()
                page.wait_for_function(
                    "document.querySelector('#marketGrid').textContent.includes('react_text_to_sql')"
                )
                market_text = page.locator("#marketGrid").inner_text()
                page.get_by_role("button", name="A2A 协作").click()
                page.wait_for_function(
                    "document.querySelector('#capabilityRows').textContent.includes('query_market_database')"
                )
                a2a_text = page.locator("#capabilityRows").inner_text()
                page.get_by_role("button", name="Market", exact=True).click()
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      activeViewWidth: document.querySelector('#market').getBoundingClientRect().width
                    })"""
                )
                page.evaluate("window.scrollTo(0, 0)")
                screenshot = BROWSER_ARTIFACT_DIR / f"v21_text_to_sql_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                results.append(
                    {
                        "viewport": name,
                        "market_sql_visible": all(
                            value in market_text
                            for value in (
                                "react_text_to_sql",
                                "SELECT AVG(price)",
                                "allowed",
                                "true",
                            )
                        ),
                        "a2a_sql_capability_visible": "query_market_database"
                        in a2a_text,
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
            result["market_sql_visible"]
            and result["a2a_sql_capability_visible"]
            and result["layout"]["bodyWidth"] <= result["layout"]["viewportWidth"]
            and result["layout"]["activeViewWidth"] > 0
            and result["screenshot_bytes"] > 10_000
        )
    report = {
        "version": "v21",
        "passed": all(result["passed"] for result in results) and not console_errors,
        "runtime_status_stubbed": True,
        "fixture_boundary": "offline model fixture; production SQL policy and SQLite were used",
        "task_id": state.task_id,
        "console_errors": console_errors,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
