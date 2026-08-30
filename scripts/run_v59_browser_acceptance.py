from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8469").rstrip("/")
OUTPUT = ROOT / "reports" / "v59" / "browser"
GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，库存800件，"
    "最低毛利率25%，面向游戏爱好者。已确认功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认产品形态：入耳式。"
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    console_errors: list[str] = []
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
        before = page.evaluate(
            """() => ({
              workspace: document.querySelector('.workspace').getBoundingClientRect().width,
              conversation: document.querySelector('.conversation').getBoundingClientRect().width,
              toggleInsideHistory: Boolean(document.querySelector('.history #sidebarToggle')),
              overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth
            })"""
        )
        page.locator("#sidebarToggle").click()
        after = page.evaluate(
            """() => ({
              workspace: document.querySelector('.workspace').getBoundingClientRect().width,
              conversation: document.querySelector('.conversation').getBoundingClientRect().width,
              collapsed: document.body.classList.contains('history-collapsed')
            })"""
        )
        page.locator("#sidebarToggle").click()
        tab_spacing = page.evaluate(
            """() => {
              const tabs = document.querySelector('.tabs').getBoundingClientRect();
              const tab = document.querySelector('.tab').getBoundingClientRect();
              return {top: tab.top - tabs.top, bottom: tabs.bottom - tab.bottom};
            }"""
        )
        page.screenshot(path=str(OUTPUT / "user_desktop.png"), full_page=True)

        run_response = page.request.post(
            f"{BASE_URL}/tasks/run",
            data={
                "goal": GOAL,
                "approval": {
                    "approved": True,
                    "approver": "v59_browser_acceptance",
                    "reason": "final linked acceptance",
                },
            },
        )
        state = run_response.json()
        task_id = state.get("task_id")
        linkage_response = page.request.get(f"{BASE_URL}/api/tasks/{task_id}/linkage")
        linkage = linkage_response.json()
        seller = page.request.get(f"{BASE_URL}/seller-center/state").json()
        trace = page.request.get(f"{BASE_URL}/api/traces/{state.get('run_id')}")
        page.reload(wait_until="networkidle")
        checkpoint = page.request.get(f"{BASE_URL}/tasks/{task_id}").json()

        for name, route in (
            ("ops", "/ops"),
            ("traces", "/traces"),
            ("seller_center", "/seller-center"),
        ):
            surface = browser.new_page(viewport={"width": 1440, "height": 900})
            loaded = surface.goto(f"{BASE_URL}{route}", wait_until="networkidle")
            screenshot = OUTPUT / f"{name}.png"
            surface.screenshot(path=str(screenshot), full_page=True)
            results.append(
                {
                    "surface": name,
                    "status": loaded.status if loaded else 0,
                    "screenshot": str(screenshot),
                    "screenshot_bytes": screenshot.stat().st_size,
                }
            )
            surface.close()

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile_response = mobile.goto(f"{BASE_URL}/", wait_until="networkidle")
        mobile_overflow = mobile.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        mobile_path = OUTPUT / "user_mobile.png"
        mobile.screenshot(path=str(mobile_path), full_page=True)
        mobile.close()
        browser.close()

    checks = {
        "user_surface_available": bool(response and response.status == 200),
        "four_surfaces_available": all(item["status"] == 200 for item in results),
        "toggle_belongs_to_history": before["toggleInsideHistory"],
        "collapse_expands_only_conversation": (
            after["collapsed"]
            and after["conversation"] > before["conversation"]
            and abs(after["workspace"] - before["workspace"]) <= 1
        ),
        "tab_vertical_spacing_balanced": abs(tab_spacing["top"] - tab_spacing["bottom"]) <= 2,
        "desktop_no_horizontal_overflow": not before["overflow"],
        "mobile_no_horizontal_overflow": not mobile_overflow,
        "task_completed": run_response.ok and state.get("status") == "completed",
        "linked_identity_consistent": linkage_response.ok and linkage.get("consistent") is True,
        "seller_received_same_task": (seller.get("last_execution") or {}).get("task_id") == task_id,
        "trace_available": trace.ok,
        "refresh_recovers_checkpoint": checkpoint.get("run_id") == state.get("run_id"),
        "screenshots_nonempty": all(item["screenshot_bytes"] > 10_000 for item in results)
        and mobile_path.stat().st_size > 10_000,
        "no_console_errors": not console_errors,
    }
    report = {
        "version": "v59",
        "base_url": BASE_URL,
        "passed": all(checks.values()),
        "checks": checks,
        "task_identity": {
            "task_id": task_id,
            "run_id": state.get("run_id"),
            "checkpoint_version": state.get("checkpoint_version"),
        },
        "linkage": linkage,
        "layout": {"before": before, "after": after, "tab_spacing": tab_spacing},
        "surfaces": results,
        "console_errors": console_errors,
    }
    target = ROOT / "reports" / "v59" / "browser_acceptance.json"
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
