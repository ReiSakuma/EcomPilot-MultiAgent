from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8458").rstrip("/")
OUTPUT = Path("reports/v58/visual")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    viewports = {"desktop": (1440, 960), "mobile": (390, 844)}
    results: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for page_name, path in (("user", "/"), ("ops", "/ops")):
            for viewport_name, (width, height) in viewports.items():
                page = browser.new_page(viewport={"width": width, "height": height})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
                overflow = page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"
                )
                screenshot = OUTPUT / f"{page_name}_{viewport_name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                results.append(
                    {
                        "page": page_name,
                        "viewport": viewport_name,
                        "status": response.status if response else 0,
                        "horizontal_overflow": overflow,
                        "page_errors": errors,
                        "screenshot": str(screenshot),
                        "screenshot_bytes": screenshot.stat().st_size,
                    }
                )
                page.close()
        browser.close()
    report = {
        "version": "v58",
        "base_url": BASE_URL,
        "passed": all(
            item["status"] == 200
            and not item["horizontal_overflow"]
            and not item["page_errors"]
            and item["screenshot_bytes"] > 10_000
            for item in results
        ),
        "results": results,
    }
    output = Path("reports/v58/visual_check.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
