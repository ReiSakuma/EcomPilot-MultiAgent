from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8235").rstrip("/")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v34"


DEMO_RESPONSE = {
    "protocol_version": "1.6",
    "conversation_id": "conv_visual_demo",
    "task_id": "task_visual_demo",
    "outcome": "awaiting_approval",
    "intent": {"intent": "create_listing", "confidence": 0.98, "reason": "visual fixture"},
    "data_scope": ["market_catalog", "listing_draft", "pricing_plan"],
    "assistant_message": "方案已经完成市场调研、商品文案、定价和风险检查，等待你的确认。",
    "action_summary": {"steps": [
        {"detail": "已完成无线耳机市场价格调研。", "status": "completed"},
        {"detail": "已生成商品标题与核心卖点。", "status": "completed"},
        {"detail": "已校验优惠后的毛利率。", "status": "completed"},
    ], "tool_call_count": 6},
    "model_usage": {"mode": "real_model", "actual_call_count": 3, "stub_call_count": 0},
    "approval_required": True,
    "execution_plan_hash": "a" * 64,
    "store_modified": False,
    "links": {},
    "understood_requirements": {"category": "无线耳机", "target_audience": "游戏爱好者", "cost": 95, "target_price": 300, "inventory": 800, "min_margin_rate": 0.4, "confirmed_features": ["蓝牙5.3", "游戏低延迟", "长续航", "快充", "通话降噪"]},
    "panels": [
        {"panel_id": "market", "data": {"price_band": [199, 329], "median_price": 259, "sample_size": {"competitors": 12, "reviews": 24}, "high_frequency_highlights": ["低延迟", "长续航"], "user_pain_points": ["连接稳定性", "佩戴舒适度"]}},
        {"panel_id": "listing", "data": {"title": "蓝牙5.3低延迟游戏无线耳机 长续航快充", "bullets": ["游戏低延迟，音画同步", "长续航并支持快充", "通话降噪，沟通清晰"], "keywords": ["游戏耳机", "低延迟耳机", "蓝牙5.3"], "compliance_notes": ["仅使用用户已确认功能"]}},
        {"panel_id": "strategy", "data": {"price": 300, "coupon": 20, "planned_units": 300, "launch_plan": "首月以 300 元标价测试，使用 20 元优惠券。", "margin": {"net_price": 280, "margin_rate": 0.6607}, "inventory_check": {"valid": True, "inventory": 800, "planned_units": 300, "remaining": 500}, "selected_evidence_tools": ["forecast_demand", "simulate_discount_scenarios"]}},
        {"panel_id": "review", "data": {"approved_for_execution": True, "review_notes": ["毛利、库存和宣传表达均通过检查"]}},
        {"panel_id": "execution", "data": {}},
    ],
}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        desktop = browser.new_page(viewport={"width": 1536, "height": 960})
        desktop.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        desktop.goto(f"{BASE_URL}/user", wait_until="networkidle")
        desktop.evaluate("response => renderResponse(response)", DEMO_RESPONSE)
        desktop.screenshot(path=str(OUTPUT_DIR / "workspace_desktop.png"), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        mobile.goto(f"{BASE_URL}/user", wait_until="networkidle")
        mobile.screenshot(path=str(OUTPUT_DIR / "mobile_conversation.png"), full_page=True)
        mobile.evaluate("response => renderResponse(response)", DEMO_RESPONSE)
        mobile.get_by_role("button", name="结果").click()
        mobile.screenshot(path=str(OUTPUT_DIR / "mobile_results.png"), full_page=True)

        ops = browser.new_page(viewport={"width": 1536, "height": 960})
        ops.goto(f"{BASE_URL}/ops", wait_until="networkidle")
        ops.screenshot(path=str(OUTPUT_DIR / "operations_read_only.png"), full_page=True)

        openapi = desktop.request.get(f"{BASE_URL}/openapi.json").json()
        result = {
            "status": "passed",
            "dispatch_api": "/api/copilot/messages/dispatch" in openapi.get("paths", {}),
            "sse_api": "/api/copilot/streams/{stream_id}/events" in openapi.get("paths", {}),
            "active_stream_api": "/api/copilot/conversations/{conversation_id}/active-stream" in openapi.get("paths", {}),
            "desktop_overflow": desktop.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
            "mobile_overflow": mobile.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"),
            "mobile_results_visible": mobile.locator("#workspaceContent").is_visible(),
            "mobile_conversation_hidden": not mobile.locator(".conversation").is_visible(),
            "ops_read_only": "只读" in ops.locator("body").inner_text(),
            "console_errors": console_errors,
            "screenshots": [str(path) for path in sorted(OUTPUT_DIR.glob("*.png"))],
        }
        browser.close()
    if not all((result["dispatch_api"], result["sse_api"], result["active_stream_api"], result["mobile_results_visible"], result["mobile_conversation_hidden"], result["ops_read_only"])) or result["desktop_overflow"] or result["mobile_overflow"] or console_errors:
        result["status"] = "failed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
