from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from app.access.models import default_principal
from app.agents.supervisor import Supervisor
from app.config import PROJECT_ROOT
from app.copilot.facade import ConversationFacade
from app.copilot_ui import COPILOT_HTML


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 300 元，库存 800 件，"
    "主要面向游戏爱好者，毛利率不能低于 40%。"
    "已确认功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
)
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 960},
    "mobile": {"width": 390, "height": 844},
}


def _response() -> dict:
    state = Supervisor().run(
        GOAL,
        principal=default_principal(),
        conversation_id="conv_v55_visual",
        turn_id="turn_v55_visual",
    )
    return ConversationFacade.build_response(state).model_dump(mode="json")


def _stub_bootstrap(page, html: str) -> None:
    payloads = {
        "/linked/status": {
            "ready": True,
            "issues": [],
            "llm": {"ready": True, "real_llm_enabled": True, "model": "deepseek-v4-pro"},
            "browser": {"ready": True, "real_browser_enabled": True, "backend": "playwright"},
        },
        "/api/copilot/products?limit=100": [],
    }

    def fulfill(route):
        path = route.request.url.replace("http://ecompilot.local", "")
        if path in {"", "/"}:
            route.fulfill(status=200, content_type="text/html", body=html)
            return
        if path == "/static/fonts/NotoSansCJK-Regular.ttc":
            route.fulfill(
                status=200,
                content_type="font/ttf",
                path=str(PROJECT_ROOT / "app" / "static" / "fonts" / "NotoSansCJK-Regular.ttc"),
            )
            return
        body = payloads.get(path, {"conversations": []})
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("http://ecompilot.local/**", fulfill)


def main() -> int:
    response = _response()
    output = PROJECT_ROOT / "reports" / "v55" / "visual"
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    html = COPILOT_HTML
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for name, viewport in VIEWPORTS.items():
                page = browser.new_page(viewport=viewport)
                _stub_bootstrap(page, html)
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://ecompilot.local/", wait_until="networkidle")
                page.wait_for_timeout(150)
                page.evaluate("payload => renderResponse(payload)", response)
                if name == "mobile":
                    page.evaluate("""() => {
                      document.body.classList.remove('mobile-view-chat');
                      document.body.classList.add('mobile-view-results');
                    }""")
                page.locator("#priceConfirmation.visible").wait_for()
                layout = page.evaluate("""() => {
                  const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0;
                  };
                  const targets = [...document.querySelectorAll('.price-fact,.price-option,#marketPanel .card')]
                    .filter(visible);
                  return {
                    bodyWidth: document.body.scrollWidth,
                    viewportWidth: window.innerWidth,
                    horizontalOverflow: targets.filter(element => element.scrollWidth > element.clientWidth + 1).length,
                    visibleOptions: [...document.querySelectorAll('.price-option button')].filter(visible).length,
                    confirmationVisible: visible(document.querySelector('#priceConfirmation'))
                  };
                }""")
                screenshot = output / f"price_confirmation_{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                passed = (
                    layout["bodyWidth"] <= layout["viewportWidth"]
                    and layout["horizontalOverflow"] == 0
                    and layout["visibleOptions"] == 3
                    and layout["confirmationVisible"]
                    and not errors
                    and screenshot.stat().st_size > 10_000
                )
                results.append(
                    {
                        "viewport": name,
                        "passed": passed,
                        "layout": layout,
                        "page_errors": errors,
                        "screenshot": str(screenshot),
                    }
                )
                page.close()
        finally:
            browser.close()
    report = {"passed": all(item["passed"] for item in results), "results": results}
    target = output.parent / "visual_check.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
