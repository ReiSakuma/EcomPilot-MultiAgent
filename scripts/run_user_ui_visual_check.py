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

LLM_STATUS = {
    "ready": True,
    "real_llm_enabled": True,
    "provider": "deepseek",
    "model": "visual-contract-model",
}
BROWSER_STATUS = {
    "ready": True,
    "real_browser_enabled": True,
    "backend": "playwright",
}


def stub_runtime_status(page) -> None:
    """Keep screenshot tests offline; real-provider behavior is integration-tested separately."""
    page.route(
        "**/linked/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"ready": True, "issues": [], "llm": LLM_STATUS, "browser": BROWSER_STATUS}
            ),
        ),
    )
    page.route(
        "**/user/tasks/run",
        lambda route: route.continue_(url=f"{BASE_URL}/tasks/run"),
    )
    page.route(
        "**/user/tasks/*/resume",
        lambda route: route.continue_(url=route.request.url.replace("/user/tasks/", "/tasks/")),
    )


def main() -> None:
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for name, viewport in VIEWPORTS.items():
                page = browser.new_page(viewport=viewport)
                stub_runtime_status(page)
                console_errors: list[str] = []
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.goto(f"{BASE_URL}/", wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                page.click("#generateButton")
                page.wait_for_selector("#result.visible", timeout=10_000)
                generated_title = page.locator("#decisionTitle").inner_text()
                execute_visible = page.locator("#executeButton").is_visible()
                if execute_visible:
                    page.click("#executeButton")
                    page.wait_for_function(
                        "document.querySelector('#decisionTitle').textContent.includes('已同步')",
                        timeout=10_000,
                    )
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      controlsOutside: [...document.querySelectorAll('input,textarea,button,a')]
                        .filter(el => { const r = el.getBoundingClientRect(); return r.left < 0 || r.right > window.innerWidth; })
                        .length
                    })"""
                )
                screenshot = BROWSER_ARTIFACT_DIR / f"user_workspace_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                final_title = page.locator("#decisionTitle").inner_text()
                passed = (
                    generated_title == "方案可执行，等待你的确认"
                    and execute_visible
                    and final_title == "已同步并完成核对"
                    and layout["bodyWidth"] <= layout["viewportWidth"]
                    and layout["controlsOutside"] == 0
                    and not console_errors
                    and screenshot.stat().st_size > 10_000
                )
                results.append(
                    {
                        "viewport": name,
                        "passed": passed,
                        "generated_title": generated_title,
                        "final_title": final_title,
                        "layout": layout,
                        "console_errors": console_errors,
                        "screenshot": str(screenshot),
                    }
                )
                page.close()

            page = browser.new_page(viewport=VIEWPORTS["desktop"])
            stub_runtime_status(page)
            console_errors: list[str] = []
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.goto(f"{BASE_URL}/", wait_until="networkidle")
            page.fill("#cost", "180")
            page.click("#generateButton")
            page.wait_for_selector("#result.visible", timeout=10_000)
            blocked_title = page.locator("#decisionTitle").inner_text()
            suggestions = page.locator(".suggestion").all_inner_texts()
            screenshot = BROWSER_ARTIFACT_DIR / "user_workspace_blocked.png"
            page.screenshot(path=str(screenshot), full_page=True)
            passed = (
                blocked_title == "当前方案不满足执行条件"
                and not page.locator("#executeButton").is_visible()
                and len(suggestions) == 2
                and "240.00 元" in suggestions[0]
                and "149.25 元" in suggestions[1]
                and not console_errors
            )
            results.append(
                {
                    "viewport": "blocked-case",
                    "passed": passed,
                    "blocked_title": blocked_title,
                    "suggestions": suggestions,
                    "console_errors": console_errors,
                    "screenshot": str(screenshot),
                }
            )
            page.close()
        finally:
            browser.close()
    report = {
        "runtime_status_stubbed": True,
        "pass_rate": sum(int(item["passed"]) for item in results) / len(results),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["pass_rate"] < 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
