from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.copilot.facade import ConversationFacade
from app.config import BROWSER_ARTIFACT_DIR
from app.orchestration.workflow import run_workflow


BASE_URL = os.getenv("ECOMPILOT_BROWSER_BASE_URL", "http://127.0.0.1:8147").rstrip("/")
GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价300元，主要面向游戏爱好者，"
    "库存800件，毛利率不能低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认的产品形态：未确认。运营目标：主打性价比。"
)
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 960},
    "mobile": {"width": 390, "height": 844},
}


def response_fixture(*, approved: bool) -> dict:
    state = run_workflow(GOAL, approved=approved)
    return ConversationFacade.build_response(state).model_dump(mode="json")


def main() -> None:
    waiting = response_fixture(approved=False)
    completed = response_fixture(approved=True)
    for payload in (waiting, completed):
        payload["conversation_id"] = "conv_visual_history"
        payload["turn_id"] = "turn_visual_history"
        payload["thread_id"] = "conv_visual_history"
    now = "2026-08-27T10:00:00+00:00"
    conversations = {
        "conversations": [
            {
                "conversation_id": "conv_visual_history",
                "tenant_id": "tenant_demo",
                "title": "无线耳机上新方案",
                "status": "active",
                "active_product_id": None,
                "summary": "",
                "summary_version": 0,
                "created_at": now,
                "updated_at": now,
                "last_message": completed["assistant_message"],
                "last_task_status": "completed",
                "message_count": 3,
            }
        ]
    }
    detail = {
        "detail": {
            "conversation": {
                key: value
                for key, value in conversations["conversations"][0].items()
                if key not in {"last_message", "last_task_status", "message_count"}
            },
            "messages": [
                {"message_id":"msg_visual_user","conversation_id":"conv_visual_history","turn_id":"turn_visual_history","tenant_id":"tenant_demo","role":"user","content":GOAL,"intent":"create_listing","task_id":None,"product_refs":[],"created_at":now},
                {"message_id":"msg_visual_wait","conversation_id":"conv_visual_history","turn_id":"turn_visual_history","tenant_id":"tenant_demo","role":"assistant","content":waiting["assistant_message"],"intent":"create_listing","task_id":completed["task_id"],"product_refs":[],"created_at":now},
                {"message_id":"msg_visual_done","conversation_id":"conv_visual_history","turn_id":"turn_visual_history","tenant_id":"tenant_demo","role":"assistant","content":completed["assistant_message"],"intent":"create_listing","task_id":completed["task_id"],"product_refs":[],"created_at":now},
            ],
            "turns": [],
            "tasks": [],
        },
        "latest_response": completed,
    }
    linked = {
        "ready": True,
        "issues": [],
        "llm": {
            "ready": True,
            "real_llm_enabled": True,
            "provider": "deepseek",
            "model": "visual-contract-model",
        },
        "browser": {
            "ready": True,
            "real_browser_enabled": True,
            "backend": "playwright",
        },
    }
    BROWSER_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
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
                page.route(
                    "**/linked/status",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(linked),
                    ),
                )
                page.route(
                    "**/api/copilot/messages",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(waiting, ensure_ascii=False),
                    ),
                )
                page.route(
                    "**/api/copilot/conversations?*",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(conversations, ensure_ascii=False),
                    ),
                )
                page.route(
                    "**/api/copilot/conversations/conv_visual_history",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(detail, ensure_ascii=False),
                    ),
                )
                page.route(
                    "**/api/copilot/tasks/*/approve",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(completed, ensure_ascii=False),
                    ),
                )
                page.goto(f"{BASE_URL}/", wait_until="networkidle")
                page.click("#exampleButton")
                page.click("#sendButton")
                page.wait_for_selector("#workspaceContent.visible", timeout=10_000)
                generated_title = page.locator("#decisionTitle").inner_text()
                assistant_text = page.locator(".message.assistant").last.inner_text()
                requirements_visible = page.locator("#requirementsEditor").is_visible()
                page.click("#executeButton")
                page.wait_for_function(
                    "document.querySelector('#decisionTitle').textContent.includes('已同步')",
                    timeout=10_000,
                )
                page.reload(wait_until="networkidle")
                if name == "desktop":
                    page.click('[data-conversation="conv_visual_history"]')
                else:
                    page.click("#historyToggle")
                    page.click('[data-conversation="conv_visual_history"]')
                page.wait_for_function(
                    "document.querySelector('#decisionTitle').textContent.includes('已同步')",
                    timeout=10_000,
                )
                page.wait_for_function(
                    "!document.body.classList.contains('history-open')",
                    timeout=10_000,
                )
                page.wait_for_timeout(250)
                history_restored = page.locator(".message.user").count() == 1 and page.locator(".message.assistant").count() == 2
                layout = page.evaluate(
                    """() => ({
                      bodyWidth: document.body.scrollWidth,
                      viewportWidth: window.innerWidth,
                      outside: [...document.querySelectorAll('button,input,textarea,a')]
                        .filter(el => getComputedStyle(el).visibility !== 'hidden')
                        .filter(el => { const r=el.getBoundingClientRect(); return r.left < 0 || r.right > window.innerWidth; }).length
                    })"""
                )
                screenshot = BROWSER_ARTIFACT_DIR / f"v28_conversation_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                passed = (
                    generated_title == "方案已准备好，等待你的确认"
                    and "本次模型调用 0 次" in assistant_text
                    and requirements_visible
                    and page.locator("#decisionTitle").inner_text() == "已同步并完成核对"
                    and history_restored
                    and layout["bodyWidth"] <= layout["viewportWidth"]
                    and layout["outside"] == 0
                    and not console_errors
                    and screenshot.stat().st_size > 10_000
                )
                results.append(
                    {
                        "viewport": name,
                        "passed": passed,
                        "generated_title": generated_title,
                        "requirements_visible": requirements_visible,
                        "history_restored": history_restored,
                        "layout": layout,
                        "console_errors": console_errors,
                        "screenshot": str(screenshot),
                    }
                )
                page.close()
        finally:
            browser.close()
    report = {
        "runtime_status_stubbed": True,
        "purpose": "V28 persistent conversation UI contract; real provider checks remain separate",
        "pass_rate": sum(int(item["passed"]) for item in results) / len(results),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["pass_rate"] < 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
