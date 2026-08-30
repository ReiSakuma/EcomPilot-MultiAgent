from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.config import BROWSER_ARTIFACT_DIR


BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8144").rstrip("/")
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 1000},
    "mobile": {"width": 390, "height": 844},
}


def main() -> None:
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for name, viewport in VIEWPORTS.items():
                task_response = playwright.request.new_context().post(
                    f"{BASE_URL}/tasks/run",
                    data={
                        "goal": (
                            "我要上架一款成本95元、售价199元、库存800件的无线耳机，"
                            "面向大学生，毛利率不低于25%。"
                        ),
                        "approval": {"approved": True, "approver": "visual-check"},
                    },
                )
                if not task_response.ok:
                    raise RuntimeError(f"task setup failed: {task_response.text()}")
                task_id = task_response.json()["task_id"]
                page = browser.new_page(viewport=viewport)
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.goto(
                    f"{BASE_URL}/ops?task_id={task_id}&pin=1",
                    wait_until="networkidle",
                )
                page.wait_for_function(
                    "document.querySelector('#taskStatus').textContent.includes('已完成')"
                )
                page.get_by_role("button", name="Execution", exact=True).click()
                page.wait_for_function(
                    "document.querySelector('#executionGrid').textContent.includes('process_local_partitioned_memory')"
                )
                text = page.locator("#execution").inner_text()
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      activeViewWidth: document.querySelector('#execution').getBoundingClientRect().width
                    })"""
                )
                page.evaluate("window.scrollTo(0, 0)")
                screenshot = BROWSER_ARTIFACT_DIR / f"v25_execution_isolation_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                results.append(
                    {
                        "viewport": name,
                        "execution_evidence_visible": all(
                            value in text
                            for value in (
                                "tenant_demo",
                                "Seller Center 分区",
                                "process_local_partitioned_memory",
                                "一次性浏览器票据",
                                "plan_fingerprint",
                                "幂等命名空间",
                                "records.json",
                                "浏览器产物分区",
                                "other_tenant_ids_exposed: false",
                                "执行绑定",
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
            result["execution_evidence_visible"]
            and result["layout"]["bodyWidth"] <= result["layout"]["viewportWidth"]
            and result["layout"]["activeViewWidth"] > 0
            and result["screenshot_bytes"] > 10_000
        )
    report = {
        "version": "v25",
        "passed": all(item["passed"] for item in results) and not console_errors,
        "console_errors": console_errors,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
