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


def main() -> None:
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for name, viewport in VIEWPORTS.items():
                page = browser.new_page(viewport=viewport)
                console_errors: list[str] = []
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.goto(f"{BASE_URL}/seller-center/editor")
                page.evaluate("document.fonts.ready")
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      controlsOutside: [...document.querySelectorAll('input,select,textarea,button')]
                        .filter(el => { const r = el.getBoundingClientRect(); return r.left < 0 || r.right > window.innerWidth; })
                        .length
                    })"""
                )
                screenshot = BROWSER_ARTIFACT_DIR / f"visual_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                passed = (
                    layout["bodyWidth"] <= layout["viewportWidth"]
                    and layout["controlsOutside"] == 0
                    and not console_errors
                    and screenshot.stat().st_size > 10_000
                )
                results.append(
                    {
                        "viewport": name,
                        "passed": passed,
                        "layout": layout,
                        "console_errors": console_errors,
                        "screenshot": str(screenshot),
                    }
                )
                page.close()
        finally:
            browser.close()
    report = {"pass_rate": sum(int(item["passed"]) for item in results) / len(results), "results": results}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["pass_rate"] < 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
