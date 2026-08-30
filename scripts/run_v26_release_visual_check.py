from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.config import BROWSER_ARTIFACT_DIR


BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8146").rstrip("/")
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 390, "height": 844},
}


def main() -> None:
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
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
                page.goto(f"{BASE_URL}/ops", wait_until="networkidle")
                page.get_by_role("button", name="Release", exact=True).click()
                page.wait_for_function(
                    "document.querySelector('#releaseStats').textContent.includes('面试最终版就绪')"
                )
                release_text = page.locator("#release").inner_text()
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      activeViewWidth: document.querySelector('#release').getBoundingClientRect().width,
                      threatRows: document.querySelectorAll('#threatRows tr').length,
                      evidenceRows: document.querySelectorAll('#evidenceRows tr').length
                    })"""
                )
                page.evaluate("window.scrollTo(0, 0)")
                screenshot = BROWSER_ARTIFACT_DIR / f"v26_release_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                results.append(
                    {
                        "viewport": name,
                        "release_evidence_visible": all(
                            value in release_text
                            for value in (
                                "面试最终版就绪",
                                "10/10",
                                "SHA-256 通过",
                                "未声明生产就绪",
                                "威胁与控制矩阵",
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
        layout = result["layout"]
        result["passed"] = (
            result["release_evidence_visible"]
            and layout["bodyWidth"] <= layout["viewportWidth"]
            and layout["activeViewWidth"] > 0
            and layout["threatRows"] == 10
            and layout["evidenceRows"] >= 20
            and result["screenshot_bytes"] > 10_000
        )
    report = {
        "version": "v26-interview-final",
        "passed": all(item["passed"] for item in results) and not console_errors,
        "console_errors": console_errors,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
