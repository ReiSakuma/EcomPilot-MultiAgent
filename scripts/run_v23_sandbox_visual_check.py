from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.config import BROWSER_ARTIFACT_DIR
from scripts.run_v21_acceptance import run_fixture_task


BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8141").rstrip("/")
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
                page.get_by_role("button", name="Sandbox", exact=True).click()
                page.wait_for_function(
                    "document.querySelector('#sandboxGrid').textContent.includes('subprocess')"
                )
                text = page.locator("#sandboxGrid").inner_text()
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      activeViewWidth: document.querySelector('#sandbox').getBoundingClientRect().width
                    })"""
                )
                page.evaluate("window.scrollTo(0, 0)")
                screenshot = BROWSER_ARTIFACT_DIR / f"v23_process_sandbox_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                results.append(
                    {
                        "viewport": name,
                        "sandbox_evidence_visible": all(
                            value in text
                            for value in (
                                "subprocess",
                                "separate_process: true",
                                "site_packages_disabled: true",
                                "shell_enabled: false",
                                "secret_environment_present: false",
                                "sqlite_authorizer: true",
                                "container: false",
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
            result["sandbox_evidence_visible"]
            and result["layout"]["bodyWidth"] <= result["layout"]["viewportWidth"]
            and result["layout"]["activeViewWidth"] > 0
            and result["screenshot_bytes"] > 10_000
        )
    report = {
        "version": "v23",
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
