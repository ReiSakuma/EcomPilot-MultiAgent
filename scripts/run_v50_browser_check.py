from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8250").rstrip("/")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v50"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_status: dict[str, int] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1536, "height": 960})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        response = page.goto(f"{BASE_URL}/", wait_until="networkidle")
        page_status["user"] = response.status if response else 0
        quick_actions = page.locator("[data-example]").count()
        page.locator('[data-example="market"]').first.click()
        market_prompt_loaded = "价格区间" in page.locator("#messageInput").input_value()
        page.evaluate(
            "showExecutionReceipt('running', '后台执行任务已安全保存。页面刷新后会自动恢复进度。', 'job_visual')"
        )
        receipt_visible = page.locator("#executionReceipt").evaluate(
            "element => element.classList.contains('visible') && element.title.includes('job_visual')"
        )
        page.screenshot(path=str(OUTPUT_DIR / "user_desktop.png"), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(f"{BASE_URL}/", wait_until="networkidle")
        mobile_overflow = mobile.evaluate("document.body.scrollWidth > innerWidth")
        mobile.screenshot(path=str(OUTPUT_DIR / "user_mobile.png"), full_page=True)
        mobile.close()

        for name, route in (
            ("ops", "/ops"),
            ("traces", "/traces"),
            ("seller_center", "/seller-center"),
        ):
            check = browser.new_page(viewport={"width": 1440, "height": 900})
            check.on(
                "console",
                lambda message: console_errors.append(f"{name}: {message.text}")
                if message.type == "error"
                else None,
            )
            loaded = check.goto(f"{BASE_URL}{route}", wait_until="networkidle")
            page_status[name] = loaded.status if loaded else 0
            check.screenshot(path=str(OUTPUT_DIR / f"{name}.png"), full_page=True)
            check.close()
        browser.close()

    checks = {
        "all_four_surfaces_available": all(value == 200 for value in page_status.values()),
        "three_user_intent_examples": quick_actions == 3,
        "market_example_fills_composer": market_prompt_loaded,
        "durable_receipt_is_user_visible": receipt_visible,
        "mobile_has_no_horizontal_overflow": not mobile_overflow,
        "no_console_errors": not console_errors,
    }
    payload = {
        "release": "v50-final-ui",
        "passed": all(checks.values()),
        "checks": checks,
        "page_status": page_status,
        "console_errors": console_errors,
    }
    (OUTPUT_DIR / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
