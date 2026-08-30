from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8213").rstrip("/")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v31"


def _response() -> dict:
    return {
        "protocol_version": "1.3",
        "response_id": "response_visual_v31",
        "conversation_id": "conv_visual_v31",
        "turn_id": "turn_visual_v31",
        "thread_id": "conv_visual_v31",
        "task_id": "task_visual_v31",
        "run_id": "run_visual_v31",
        "outcome": "read_only_completed",
        "intent": {
            "intent": "product_performance",
            "original_intent": None,
            "confidence": 1.0,
            "rationale": "用户正在查询已有商品的历史销售表现。",
            "risk_level": "read",
            "data_scope": ["product_ledger", "daily_product_metrics", "inventory_movements"],
        },
        "assessment": {
            "mode": "read_only",
            "field_evidence": [],
            "missing_fields": [],
            "explicitly_unknown_fields": [],
            "proposed_workflow": "product_performance_read_only",
            "allowed_scopes": ["product.read", "analytics.read"],
            "approval_required": False,
            "clarification_question": None,
            "clarification_round": 0,
        },
        "data_scope": ["product_ledger", "daily_product_metrics", "inventory_movements"],
        "entity_refs": ["product_visual_v31"],
        "assistant_message": "最近 30 天共售出 141 件，销售额 42300.00 元，转化率 6.48%，期末库存 301 件。当前为模拟演示数据。",
        "understood_requirements": {"period_label": "最近 30 天"},
        "action_summary": {
            "headline": "已完成 1/1 个业务环节。",
            "steps": [{
                "step_id": "analytics", "title": "查询商品销售表现", "status": "completed",
                "detail": "查询商品销售表现已完成。", "agent_name": "analytics_agent",
                "tool_names": ["get_sales_metrics", "compare_sales_periods", "get_inventory_history"],
                "artifact_refs": ["artifact_analytics_visual"], "trace_refs": [],
            }],
            "completed_step_count": 1, "total_step_count": 1, "tool_call_count": 3,
            "trace_event_count": 12, "execution_performed": False,
        },
        "panels": [{
            "panel_id": "analytics", "title": "销售表现", "status": "completed",
            "summary": "最近 30 天售出 141 件，销售额 42300.00 元。",
            "data": {
                "product_id": "product_visual_v31",
                "period": {"start_date": "2026-07-30", "end_date": "2026-08-28", "label": "最近 30 天"},
                "sales": {"metrics": {"impressions": 28460, "clicks": 1958, "orders": 127, "units_sold": 141, "revenue": 42300, "refunds": 2, "conversion_rate": 0.0648, "ending_inventory": 301}},
                "comparison": {"change": {"units_sold_rate": 0.2155}},
                "campaigns": None,
                "inventory": {"starting_inventory": 442, "ending_inventory": 301},
                "selected_evidence_tools": ["get_sales_metrics", "compare_sales_periods", "get_inventory_history"],
                "narrative": "最近 30 天销售表现已完成。",
                "generation_mode": "react_read_only",
                "source_type": "synthetic_demo",
                "source_updated_at": "2026-08-28T10:00:00+08:00",
            },
            "source_agents": ["analytics_agent"],
            "artifact_refs": ["artifact_analytics_visual"],
        }],
        "model_usage": {"configured_provider": "deepseek", "configured_model": "deepseek-v4-pro", "recorded_call_count": 3, "actual_call_count": 3, "stub_call_count": 0, "mode": "real_model", "providers_used": ["deepseek"]},
        "approval_required": False,
        "store_modified": False,
        "failure": None,
        "links": {"operations": "/ops", "trace": "/traces", "seller_center": "/seller-center"},
    }


def _stub(page: Page) -> None:
    page.route("**/linked/status", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"ready": True, "llm": {"ready": True, "real_llm_enabled": True, "model": "deepseek-v4-pro"}, "browser": {"ready": True, "real_browser_enabled": True}})))
    page.route("**/api/copilot/conversations?*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"conversations":[]}'))
    page.route("**/api/copilot/messages", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_response(), ensure_ascii=False)))


def _check(page: Page, name: str) -> dict:
    page.goto(BASE_URL, wait_until="networkidle")
    page.fill("#messageInput", "上次那个耳机最近30天销量和库存趋势怎么样？")
    page.click("#sendButton")
    page.wait_for_selector("[data-business-panel='analytics']:visible")
    page.click("[data-business-panel='analytics']")
    page.wait_for_function("document.querySelector('#analyticsSales').textContent.includes('42300')")
    overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    screenshot = OUTPUT_DIR / f"product_analytics_{name}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    return {
        "viewport": page.viewport_size,
        "horizontal_overflow": overflow,
        "analytics_visible": page.locator("#analyticsPanel").is_visible(),
        "source_visible": "模拟演示数据" in page.locator("#analyticsSource").inner_text(),
        "no_approval_button": not page.locator("#executeButton").is_visible(),
        "screenshot": str(screenshot),
        "screenshot_bytes": screenshot.stat().st_size,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        results = []
        for name, viewport in (("desktop", {"width": 1440, "height": 960}), ("mobile", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=viewport)
            _stub(page)
            results.append(_check(page, name))
        browser.close()
    passed = all(item["analytics_visible"] and item["source_visible"] and item["no_approval_button"] and not item["horizontal_overflow"] and item["screenshot_bytes"] > 10_000 for item in results)
    report = {"passed": passed, "views": results}
    (OUTPUT_DIR / "visual_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
