from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8180").rstrip("/")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v30"


def _response() -> dict:
    return {
        "protocol_version": "1.2",
        "response_id": "response_visual_v30",
        "conversation_id": "conv_visual_v30",
        "turn_id": "turn_visual_v30",
        "thread_id": "conv_visual_v30",
        "task_id": "task_visual_v30",
        "run_id": "run_visual_v30",
        "outcome": "answered",
        "intent": {
            "intent": "product_detail",
            "original_intent": None,
            "confidence": 1.0,
            "rationale": "商品身份索引定位",
            "risk_level": "read",
            "data_scope": ["product_ledger", "task_product_links", "seller_snapshot"],
        },
        "assessment": {
            "mode": "read_only",
            "field_evidence": [],
            "missing_fields": [],
            "explicitly_unknown_fields": [],
            "proposed_workflow": "product_detail_lookup",
            "allowed_scopes": ["product.read", "task.read"],
            "approval_required": False,
            "clarification_question": None,
            "clarification_round": 0,
        },
        "data_scope": ["product_ledger", "task_product_links", "seller_snapshot"],
        "entity_refs": ["product_visual_v30"],
        "assistant_message": "已找到游戏无线耳机，以下是商品档案和执行历史。",
        "understood_requirements": {"query": "查看这个商品详情"},
        "action_summary": {
            "headline": "已读取商品账本、任务关联和店铺快照。",
            "steps": [],
            "completed_step_count": 0,
            "total_step_count": 0,
            "tool_call_count": 0,
            "trace_event_count": 0,
            "execution_performed": False,
        },
        "panels": [
            {
                "panel_id": "product",
                "title": "商品档案",
                "status": "completed",
                "summary": "游戏无线耳机 · draft",
                "data": {
                    "product_id": "product_visual_v30",
                    "sku": "SKU-VISUAL-V30",
                    "title": "蓝牙5.3游戏无线耳机",
                    "category": "无线耳机",
                    "status": "draft",
                    "source_task_id": "task_visual_v30",
                    "price": 300,
                    "stock": 800,
                },
                "source_agents": ["product_ledger"],
                "artifact_refs": ["artifact_listing_visual"],
            },
            {
                "panel_id": "timeline",
                "title": "商品时间线",
                "status": "completed",
                "summary": "记录了 3 个事件。",
                "data": {
                    "events": [
                        {"summary": "商品页面方案已生成", "occurred_at": "2026-08-28T09:00:00Z"},
                        {"summary": "商品方案已通过执行前审核", "occurred_at": "2026-08-28T09:00:01Z"},
                        {"summary": "商品信息已写入并回读模拟店铺", "occurred_at": "2026-08-28T09:00:02Z"},
                    ],
                    "task_links": [],
                },
                "source_agents": ["product_ledger"],
                "artifact_refs": [],
            },
        ],
        "model_usage": {
            "configured_provider": "deepseek",
            "configured_model": "deepseek-v4-pro",
            "recorded_call_count": 0,
            "actual_call_count": 0,
            "stub_call_count": 0,
            "mode": "no_model_call",
            "providers_used": [],
        },
        "approval_required": False,
        "store_modified": False,
        "failure": None,
        "links": {"operations": "/ops", "trace": "/traces", "seller_center": "/seller-center"},
    }


def _stub(page: Page) -> None:
    page.route(
        "**/linked/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ready": True,
                    "llm": {"ready": True, "real_llm_enabled": True, "model": "deepseek-v4-pro"},
                    "browser": {"ready": True, "real_browser_enabled": True},
                }
            ),
        ),
    )
    page.route(
        "**/api/copilot/conversations?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"conversations":[]}'),
    )
    page.route(
        "**/api/copilot/messages",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_response(), ensure_ascii=False)
        ),
    )


def _check_view(page: Page, name: str) -> dict:
    page.goto(BASE_URL, wait_until="networkidle")
    page.fill("#messageInput", "查看这个商品详情")
    page.click("#sendButton")
    page.wait_for_selector("[data-business-panel='product']:visible")
    page.click("[data-business-panel='product']")
    page.wait_for_function(
        "document.querySelector('#productIdentity').textContent.includes('product_visual_v30')"
    )
    product_visible = page.locator("#productIdentity").is_visible()
    page.click("[data-business-panel='timeline']")
    page.wait_for_function(
        "document.querySelector('#productTimeline').textContent.includes('写入并回读模拟店铺')"
    )
    overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    screenshot = OUTPUT_DIR / f"{name}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    return {
        "viewport": page.viewport_size,
        "horizontal_overflow": overflow,
        "product_visible": product_visible,
        "timeline_visible": page.locator("#productTimeline").is_visible(),
        "screenshot": str(screenshot),
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        desktop = browser.new_page(viewport={"width": 1440, "height": 960})
        desktop.on("pageerror", lambda error: print(f"desktop pageerror: {error}"))
        desktop.on("console", lambda message: print(f"desktop console: {message.text}") if message.type == "error" else None)
        _stub(desktop)
        desktop_result = _check_view(desktop, "product_timeline_desktop")
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("pageerror", lambda error: print(f"mobile pageerror: {error}"))
        _stub(mobile)
        mobile_result = _check_view(mobile, "product_timeline_mobile")
        browser.close()
    report = {
        "passed": all(
            item["product_visible"] and item["timeline_visible"] and not item["horizontal_overflow"]
            for item in (desktop_result, mobile_result)
        ),
        "desktop": desktop_result,
        "mobile": mobile_result,
    }
    (OUTPUT_DIR / "visual_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
