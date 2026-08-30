from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


BASE_URL = os.getenv("ECOMPILOT_BROWSER_BASE_URL", "http://127.0.0.1:8131").rstrip("/")


def stub_user_runtime(page) -> None:
    linked_status = {
        "ready": True,
        "issues": [],
        "llm": {
            "ready": True,
            "real_llm_enabled": True,
            "provider": "deepseek",
            "model": "offline-linkage-contract",
        },
        "browser": {
            "ready": True,
            "real_browser_enabled": True,
            "backend": "playwright",
        },
    }
    page.route(
        "**/linked/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(linked_status),
        ),
    )
    page.route(
        "**/user/tasks/run",
        lambda route: route.continue_(url=f"{BASE_URL}/tasks/run"),
    )
    page.route(
        "**/user/tasks/*/resume",
        lambda route: route.continue_(
            url=route.request.url.replace("/user/tasks/", "/tasks/")
        ),
    )


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.request.post(f"{BASE_URL}/seller-center/reset")

        user = context.new_page()
        stub_user_runtime(user)
        ops = context.new_page()
        traces = context.new_page()
        seller = context.new_page()

        ops.goto(f"{BASE_URL}/ops", wait_until="domcontentloaded")
        traces.goto(f"{BASE_URL}/traces", wait_until="domcontentloaded")
        seller.goto(f"{BASE_URL}/seller-center", wait_until="domcontentloaded")
        user.goto(f"{BASE_URL}/", wait_until="networkidle")
        user.click("#generateButton")
        user.wait_for_selector("#result.visible", timeout=10_000)

        task_href = user.locator("#opsLink").get_attribute("href") or ""
        run_href = user.locator("#traceLink").get_attribute("href") or ""
        task_id = parse_qs(urlparse(task_href).query)["task_id"][0]
        run_id = parse_qs(urlparse(run_href).query)["run_id"][0]

        ops.wait_for_function(
            "([taskId]) => document.querySelector('#rawJson').textContent.includes(taskId)",
            arg=[task_id],
            timeout=10_000,
        )
        traces.wait_for_function(
            "([runId]) => document.querySelector('#title').textContent.includes(runId)",
            arg=[run_id],
            timeout=10_000,
        )

        user.click("#executeButton")
        user.wait_for_function(
            "document.querySelector('#decisionTitle').textContent.includes('已同步')",
            timeout=10_000,
        )
        ops.wait_for_function(
            "document.querySelector('#taskStatus').textContent === '已完成'",
            timeout=10_000,
        )
        seller.wait_for_function(
            "document.querySelector('#state').textContent.includes('wireless_earbud_draft')",
            timeout=10_000,
        )

        report = {
            "passed": True,
            "runtime_status_stubbed": True,
            "purpose": "offline multi-page linkage contract only",
            "task_id": task_id,
            "run_id": run_id,
            "user_status": user.locator("#decisionTitle").inner_text(),
            "ops_status": ops.locator("#taskStatus").inner_text(),
            "trace_title": traces.locator("#title").inner_text(),
            "seller_center_updated": "wireless_earbud_draft"
            in seller.locator("#state").inner_text(),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
