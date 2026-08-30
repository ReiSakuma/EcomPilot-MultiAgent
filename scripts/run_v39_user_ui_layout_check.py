from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8243").rstrip("/")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v39" / "user_ui_layout"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error" else None,
        )
        page.goto(f"{BASE_URL}/", wait_until="networkidle")
        page.wait_for_selector(".history-item")
        page.evaluate(
            """() => {
              document.querySelector('#workspaceEmpty').style.display = 'none';
              document.querySelector('#workspaceContent').classList.add('visible');
            }"""
        )
        page.locator("[data-panel='marketPanel']").click()
        expanded = page.evaluate(
            """() => {
              const history = document.querySelector('.history').getBoundingClientRect();
              const conversation = document.querySelector('.conversation').getBoundingClientRect();
              const workspace = document.querySelector('.workspace').getBoundingClientRect();
              const items = [...document.querySelectorAll('.history-item')];
              const tabs = document.querySelector('#workspaceTabs').getBoundingClientRect();
              const marketTab = document.querySelector('[data-panel="marketPanel"]');
              const marketText = document.createRange();
              marketText.selectNodeContents(marketTab);
              const marketTextRect = marketText.getBoundingClientRect();
              return {
                historyRight: history.right,
                historyWidth: history.width,
                conversationLeft: conversation.left,
                conversationWidth: conversation.width,
                workspaceLeft: workspace.left,
                workspaceWidth: workspace.width,
                maxItemRight: Math.max(...items.map(item => item.getBoundingClientRect().right)),
                historyScrollWidth: document.querySelector('.history').scrollWidth,
                historyClientWidth: document.querySelector('.history').clientWidth,
                titleOverflow: getComputedStyle(items[0].querySelector('strong')).textOverflow,
                tabTextTopGapPx: marketTextRect.top - tabs.top,
                tabTextBottomGapPx: tabs.bottom - marketTextRect.bottom,
                toggleInsideHistory: document.querySelector('.history').contains(document.querySelector('#sidebarToggle')),
                bodyWidth: document.body.scrollWidth,
                viewportWidth: innerWidth
              };
            }"""
        )
        page.screenshot(path=str(OUTPUT_DIR / "expanded_desktop.png"), full_page=True)

        page.locator("#sidebarToggle").click()
        collapsed = page.evaluate(
            """() => {
              const history = document.querySelector('.history').getBoundingClientRect();
              const conversation = document.querySelector('.conversation').getBoundingClientRect();
              const workspace = document.querySelector('.workspace').getBoundingClientRect();
              return {
                collapsed: document.body.classList.contains('history-collapsed'),
                historyDisplay: getComputedStyle(document.querySelector('.history')).display,
                historyWidth: history.width,
                expanded: document.querySelector('#sidebarToggle').getAttribute('aria-expanded'),
                conversationLeft: conversation.left,
                conversationWidth: conversation.width,
                workspaceLeft: workspace.left,
                workspaceWidth: workspace.width,
                toggleInsideHistory: document.querySelector('.history').contains(document.querySelector('#sidebarToggle'))
              };
            }"""
        )
        page.screenshot(path=str(OUTPUT_DIR / "collapsed_desktop.png"), full_page=True)
        page.reload(wait_until="networkidle")
        persisted = page.evaluate(
            "document.body.classList.contains('history-collapsed') && document.querySelector('#sidebarToggle').getAttribute('aria-expanded') === 'false'"
        )
        page.locator("#sidebarToggle").click()
        restored = page.evaluate(
            "!document.body.classList.contains('history-collapsed') && getComputedStyle(document.querySelector('.history')).display !== 'none'"
        )

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.goto(f"{BASE_URL}/", wait_until="networkidle")
        mobile_result = mobile.evaluate(
            """() => ({
              desktopToggleHidden: getComputedStyle(document.querySelector('#sidebarToggle')).display === 'none',
              bodyWidth: document.body.scrollWidth,
              viewportWidth: innerWidth
            })"""
        )
        mobile.screenshot(path=str(OUTPUT_DIR / "mobile.png"), full_page=True)
        browser.close()

    checks = {
        "history_items_stay_inside_sidebar": (
            expanded["maxItemRight"] <= expanded["historyRight"] + 0.5
            and expanded["historyRight"] <= expanded["conversationLeft"] + 0.5
            and expanded["historyScrollWidth"] <= expanded["historyClientWidth"]
        ),
        "long_titles_use_ellipsis": expanded["titleOverflow"] == "ellipsis",
        "market_tab_is_vertically_centered": (
            expanded["tabTextTopGapPx"] >= 10
            and expanded["tabTextBottomGapPx"] >= 10
            and abs(expanded["tabTextTopGapPx"] - expanded["tabTextBottomGapPx"]) <= 1.5
        ),
        "collapse_control_belongs_to_history": (
            expanded["toggleInsideHistory"] and collapsed["toggleInsideHistory"]
        ),
        "desktop_sidebar_collapses": (
            collapsed["collapsed"]
            and collapsed["historyDisplay"] != "none"
            and collapsed["historyWidth"] <= 49
            and collapsed["expanded"] == "false"
            and collapsed["conversationWidth"] > expanded["conversationWidth"]
        ),
        "collapse_only_expands_conversation": (
            abs(collapsed["workspaceLeft"] - expanded["workspaceLeft"]) <= 1.5
            and abs(collapsed["workspaceWidth"] - expanded["workspaceWidth"]) <= 1.5
        ),
        "collapsed_state_persists_after_reload": bool(persisted),
        "sidebar_can_expand_again": bool(restored),
        "desktop_has_no_horizontal_overflow": expanded["bodyWidth"] <= expanded["viewportWidth"],
        "mobile_keeps_drawer_control_only": mobile_result["desktopToggleHidden"],
        "mobile_has_no_horizontal_overflow": mobile_result["bodyWidth"] <= mobile_result["viewportWidth"],
        "no_console_errors": not console_errors,
    }
    report = {
        "version": "v39-user-ui-layout-fix",
        "passed": all(checks.values()),
        "checks": checks,
        "measurements": {"expanded": expanded, "collapsed": collapsed, "mobile": mobile_result},
        "console_errors": console_errors,
    }
    (OUTPUT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
