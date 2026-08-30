from __future__ import annotations

import json
import re
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

from app.browser.tickets import BrowserTicketStore
from app.access.context import current_tenant_id
from app.config import (
    BROWSER_ARTIFACT_DIR,
    BROWSER_BACKEND,
    BROWSER_BASE_URL,
    BROWSER_HEADLESS,
    BROWSER_TIMEOUT_MS,
)
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.store import SELLER_CENTER_STORE


class BrowserBackendError(RuntimeError):
    safe_to_retry = False


class BrowserConfigurationError(BrowserBackendError):
    pass


class MockBrowserBackend:
    name = "mock"

    def execute(self, plan: ExecutionPlan, execution_key: str) -> dict[str, Any]:
        tenant_id = current_tenant_id()
        applied = SELLER_CENTER_STORE.apply_execution_plan(plan)
        verification = SELLER_CENTER_STORE.verify_execution_plan(plan)
        return {
            **applied,
            "backend": self.name,
            "tenant_id": tenant_id,
            "actions": [{"action": "store.apply", "status": "completed"}],
            "verification": verification.model_dump(mode="json"),
        }

    def verify(self, plan: ExecutionPlan) -> dict[str, Any]:
        tenant_id = current_tenant_id()
        result = SELLER_CENTER_STORE.verify_execution_plan(plan).model_dump(mode="json")
        return {
            **result,
            "backend": self.name,
            "tenant_id": tenant_id,
            "actions": [{"action": "store.verify", "status": "completed"}],
        }


class PlaywrightBrowserBackend:
    name = "playwright"

    def execute(self, plan: ExecutionPlan, execution_key: str) -> dict[str, Any]:
        sync_playwright = _load_playwright()
        tenant_id = current_tenant_id()
        plan_payload = plan.model_dump(mode="json")
        ticket = BrowserTicketStore.issue(
            plan_payload, tenant_id=tenant_id, purpose="execute"
        )
        actions: list[dict[str, Any]] = []
        screenshot = _artifact_path(execution_key, "execute", tenant_id=tenant_id)
        editor_url = f"{BROWSER_BASE_URL}/seller-center/editor"

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=BROWSER_HEADLESS)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    extra_http_headers={"X-EcomPilot-Browser-Ticket": ticket},
                )
                page = context.new_page()
                page.set_default_timeout(BROWSER_TIMEOUT_MS)
                try:
                    _action(actions, "goto", "seller-center/editor", lambda: page.goto(editor_url))
                    _action(
                        actions,
                        "wait",
                        "document-fonts",
                        lambda: page.evaluate("document.fonts.ready"),
                    )
                    _select(page, actions, "operation", plan.operation)
                    _fill(page, actions, "product-id", plan.product_id)
                    _fill(page, actions, "title", plan.title or "")
                    _fill(page, actions, "price", "" if plan.price is None else str(plan.price))
                    _fill(page, actions, "stock", "" if plan.stock is None else str(plan.stock))
                    _fill(page, actions, "coupon", str(plan.coupon))
                    _fill(page, actions, "bullets", "\n".join(plan.bullets))
                    _action(
                        actions,
                        "click",
                        "submit-execution",
                        lambda: page.get_by_test_id("submit-execution").click(),
                    )
                    status = page.get_by_test_id("result-status")
                    _action(
                        actions,
                        "wait",
                        "result-status",
                        lambda: status.wait_for(state="visible"),
                    )
                    result_text = page.get_by_test_id("result-json").text_content() or "{}"
                    result = json.loads(result_text)
                    if result.get("status") != "applied":
                        raise BrowserBackendError(
                            f"seller-center page rejected execution: {result_text[:500]}"
                        )
                    _action(
                        actions,
                        "screenshot",
                        "execution-result",
                        lambda: page.screenshot(path=str(screenshot), full_page=True),
                    )
                finally:
                    browser.close()
        except BrowserBackendError:
            raise
        except Exception as exc:
            raise BrowserBackendError(f"Playwright execution failed: {exc}") from exc

        return {
            **result,
            "backend": self.name,
            "tenant_id": tenant_id,
            "ticket_purpose": "execute",
            "page_url": f"{BROWSER_BASE_URL}/seller-center/editor",
            "actions": actions,
            "screenshot_path": str(screenshot),
        }

    def verify(self, plan: ExecutionPlan) -> dict[str, Any]:
        sync_playwright = _load_playwright()
        tenant_id = current_tenant_id()
        ticket = BrowserTicketStore.issue(
            plan.model_dump(mode="json"), tenant_id=tenant_id, purpose="verify"
        )
        actions: list[dict[str, Any]] = []
        screenshot = _artifact_path(plan.product_id, "verify", tenant_id=tenant_id)
        detail_url = f"{BROWSER_BASE_URL}/seller-center/products/{quote(plan.product_id)}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=BROWSER_HEADLESS)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    extra_http_headers={"X-EcomPilot-Browser-Ticket": ticket},
                )
                page = context.new_page()
                page.set_default_timeout(BROWSER_TIMEOUT_MS)
                try:
                    _action(actions, "goto", "seller-center/product", lambda: page.goto(detail_url))
                    _action(
                        actions,
                        "wait",
                        "document-fonts",
                        lambda: page.evaluate("document.fonts.ready"),
                    )
                    observed_text = _action(
                        actions,
                        "read",
                        "observed-state",
                        lambda: page.get_by_test_id("observed-state").text_content(),
                    )
                    observed = json.loads(observed_text or "{}")
                    _action(
                        actions,
                        "screenshot",
                        "verification-result",
                        lambda: page.screenshot(path=str(screenshot), full_page=True),
                    )
                finally:
                    browser.close()
        except Exception as exc:
            raise BrowserBackendError(f"Playwright verification failed: {exc}") from exc

        result = _verify_observed(plan, observed)
        return {
            **result,
            "backend": self.name,
            "tenant_id": tenant_id,
            "ticket_purpose": "verify",
            "page_url": detail_url,
            "actions": actions,
            "screenshot_path": str(screenshot),
        }


def get_browser_backend():
    if BROWSER_BACKEND == "mock":
        return MockBrowserBackend()
    if BROWSER_BACKEND == "playwright":
        return PlaywrightBrowserBackend()
    raise BrowserConfigurationError(f"Unsupported browser backend: {BROWSER_BACKEND}")


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserConfigurationError(
            "Playwright is not installed. Run: python -m pip install playwright"
        ) from exc
    return sync_playwright


def _fill(page, actions: list[dict[str, Any]], field: str, value: str) -> None:
    _action(actions, "fill", field, lambda: page.get_by_test_id(field).fill(value))


def _select(page, actions: list[dict[str, Any]], field: str, value: str) -> None:
    """Select controls require select_option; fill() is invalid for HTML select elements."""
    _action(
        actions,
        "select",
        field,
        lambda: page.get_by_test_id(field).select_option(value),
    )


def _action(
    actions: list[dict[str, Any]],
    action: str,
    target: str,
    function: Callable[[], Any],
) -> Any:
    started = perf_counter()
    try:
        result = function()
    except Exception:
        actions.append(
            {
                "action": action,
                "target": target,
                "status": "failed",
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            }
        )
        raise
    actions.append(
        {
            "action": action,
            "target": target,
            "status": "completed",
            "duration_ms": round((perf_counter() - started) * 1000, 2),
        }
    )
    return result


def _artifact_path(
    label: str, stage: str, *, tenant_id: str | None = None
) -> Path:
    effective_tenant = tenant_id or current_tenant_id()
    tenant_directory = BROWSER_ARTIFACT_DIR / effective_tenant
    tenant_directory.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^a-zA-Z0-9_.-]", "_", label)[:80]
    return tenant_directory / f"{safe_label}_{stage}_{uuid4().hex[:8]}.png"


def _verify_observed(plan: ExecutionPlan, observed: dict[str, Any]) -> dict[str, Any]:
    product = observed.get("product")
    promotion = observed.get("promotion")
    checks: dict[str, bool] = {}
    if plan.operation in {"update_listing", "publish_listing"}:
        checks["product_exists"] = isinstance(product, dict)
        if isinstance(product, dict):
            if plan.title is not None:
                checks["title_match"] = product.get("title") == plan.title
            if plan.price is not None:
                checks["price_match"] = float(product.get("price", -1)) == plan.price
            if plan.stock is not None:
                checks["stock_match"] = int(product.get("stock", -1)) == plan.stock
            if plan.bullets:
                checks["bullets_match"] = product.get("bullets") == plan.bullets
            checks["coupon_match"] = float(product.get("coupon", -1)) == plan.coupon
            if plan.operation == "publish_listing":
                checks["status_published"] = product.get("status") == "published"
    if plan.coupon > 0:
        checks["promotion_exists"] = isinstance(promotion, dict)
        checks["promotion_coupon_match"] = (
            isinstance(promotion, dict) and float(promotion.get("coupon", -1)) == plan.coupon
        )
    errors = [name for name, passed in checks.items() if not passed]
    return {
        "verified": bool(checks) and not errors,
        "checks": checks,
        "observed": observed,
        "errors": errors,
    }
