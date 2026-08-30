from __future__ import annotations

from typing import Any

from app.access.context import tenant_scope
from app.browser.tickets import BrowserTicketStore
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.store import SELLER_CENTER_STORE


def execute_ticketed_plan(ticket: str, plan: ExecutionPlan) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    grant = BrowserTicketStore.consume_editor_plan(
        ticket, payload, purpose="execute"
    )
    approved_plan = ExecutionPlan.model_validate(grant.approved_plan)
    with tenant_scope(grant.tenant_id):
        applied = SELLER_CENTER_STORE.apply_execution_plan(approved_plan)
        verification = SELLER_CENTER_STORE.verify_execution_plan(approved_plan)
    return {
        **applied,
        "tenant_id": grant.tenant_id,
        "ticket_purpose": grant.purpose,
        "verification": verification.model_dump(mode="json"),
    }


def observed_product_state(
    product_id: str, *, tenant_id: str | None = None
) -> dict[str, Any]:
    snapshot = SELLER_CENTER_STORE.snapshot(tenant_id=tenant_id)
    return {
        "tenant_id": snapshot["tenant_id"],
        "product": snapshot["products"].get(product_id),
        "promotion": snapshot["promotions"].get(f"coupon_{product_id}"),
    }


def observed_ticketed_product_state(ticket: str, product_id: str) -> dict[str, Any]:
    grant = BrowserTicketStore.consume_product(
        ticket, product_id, purpose="verify"
    )
    with tenant_scope(grant.tenant_id):
        return observed_product_state(product_id)
