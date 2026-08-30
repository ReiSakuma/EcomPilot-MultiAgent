from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


BASE_URL = os.getenv("ECOMPILOT_BROWSER_BASE_URL", "http://127.0.0.1:8131").rstrip("/")


def query_value(href: str, name: str) -> str:
    return parse_qs(urlparse(href).query)[name][0]


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="networkidle")
        page.wait_for_function(
            "document.querySelector('#llmStatus').textContent.includes('已连接')"
            " && document.querySelector('#browserStatus').textContent.includes('已连接')",
            timeout=15_000,
        )

        page.click("#generateButton")
        page.wait_for_selector("#result.visible", timeout=300_000)
        task_id = query_value(page.locator("#opsLink").get_attribute("href") or "", "task_id")
        first_run_id = query_value(
            page.locator("#traceLink").get_attribute("href") or "", "run_id"
        )
        generation_mode = page.locator("#generationMode").inner_text()

        first_state_response = context.request.get(f"{BASE_URL}/tasks/{task_id}")
        first_state = first_state_response.json()
        first_trace_response = context.request.get(
            f"{BASE_URL}/api/traces/{first_run_id}/summary"
        )
        first_trace = first_trace_response.json()

        page.click("#executeButton")
        page.wait_for_function(
            "document.querySelector('#decisionTitle').textContent.includes('已同步')",
            timeout=120_000,
        )
        final_state_response = context.request.get(f"{BASE_URL}/tasks/{task_id}")
        final_state = final_state_response.json()
        browser_output = final_state.get("agent_outputs", {}).get("browser_agent", {})
        execution = browser_output.get("browser_result", {})
        verification = browser_output.get("verification", {})
        model_records = final_state.get("model_records", [])
        market_output = final_state.get("agent_outputs", {}).get("market_agent", {})
        sql_research = market_output.get("sql_research") or {}
        sql_policy = sql_research.get("policy") or {}
        providers = sorted({record.get("provider") for record in model_records})

        checks = {
            "user_showed_intelligent_generation": generation_mode == "智能生成",
            "five_llm_calls_recorded": len(model_records) >= 5,
            "all_calls_used_deepseek": providers == ["deepseek"],
            "trace_recorded_five_llm_calls": first_trace.get("model_call_count", 0) >= 5,
            "market_used_react_text_to_sql": market_output.get("research_mode")
            == "react_text_to_sql",
            "sql_policy_allowed_read_only_query": sql_policy.get("status") == "allowed"
            and sql_policy.get("read_only_connection") is True,
            "approval_created_child_run": final_state.get("parent_run_id") == first_run_id,
            "playwright_executed": execution.get("backend") == "playwright",
            "browser_verification_passed": verification.get("verified") is True,
            "task_completed": final_state.get("status") == "completed",
        }
        report = {
            "passed": all(checks.values()),
            "runtime_status_stubbed": False,
            "task_id": task_id,
            "initial_run_id": first_run_id,
            "final_run_id": final_state.get("run_id"),
            "model_call_count": len(model_records),
            "providers": providers,
            "browser_backend": execution.get("backend"),
            "checks": checks,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        browser.close()
        if not report["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
