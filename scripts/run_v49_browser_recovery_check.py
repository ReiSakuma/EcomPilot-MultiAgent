from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Route, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("ECOMPILOT_VISUAL_BASE_URL", "http://127.0.0.1:8249").rstrip("/")
OUTPUT_DIR = ROOT / "reports" / "browser" / "v49"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    receipt_requests: list[str] = []

    def null_receipt(route: Route) -> None:
        receipt_requests.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body="null")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.route(
            "**/api/copilot/batches/batch_browser/executions/latest",
            null_receipt,
        )
        page.goto(f"{BASE_URL}/", wait_until="networkidle")
        functions_available = page.evaluate(
            "typeof resumeLatestBatchExecution === 'function' && "
            "typeof invalidateBatchRecovery === 'function'"
        )
        page.evaluate(
            """async () => {
              currentResponse = {
                panels: [{
                  panel_id: 'requirements',
                  data: {batch_job_id: 'batch_browser', items: []}
                }]
              };
              await resumeLatestBatchExecution('batch_browser');
            }"""
        )
        first_epoch = page.evaluate("batchRecoveryEpoch")
        page.evaluate("invalidateBatchRecovery()")
        second_epoch = page.evaluate("batchRecoveryEpoch")
        page.reload(wait_until="networkidle")
        functions_after_reload = page.evaluate(
            "typeof resumeLatestBatchExecution === 'function'"
        )
        page.screenshot(path=str(OUTPUT_DIR / "reconnected_user_page.png"), full_page=True)
        browser.close()

    checks = {
        "recovery_functions_loaded": bool(functions_available),
        "latest_receipt_requested": len(receipt_requests) == 1,
        "conversation_switch_invalidates_old_poll": second_epoch == first_epoch + 1,
        "recovery_survives_page_reload": bool(functions_after_reload),
        "no_console_errors": not console_errors,
    }
    report = {
        "release": "v49-browser-receipt-recovery",
        "passed": all(checks.values()),
        "checks": checks,
        "receipt_requests": receipt_requests,
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
