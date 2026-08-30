from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.access.context import tenant_scope
from app.browser.backends import _artifact_path
from app.browser.service import (
    execute_ticketed_plan,
    observed_ticketed_product_state,
)
from app.browser.tickets import (
    BrowserTicketMismatchError,
    BrowserTicketPurposeError,
    BrowserTicketStore,
)
from app.main import (
    execution_isolation_status,
    seller_center_reset,
    seller_center_state,
)
from app.safety.idempotency import IdempotencyStore
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.store import SELLER_CENTER_STORE
from app.tools.browser_tools import (
    browser_execute,
    get_seller_center_snapshot,
    reset_seller_center,
)


def plan(title: str, product_id: str = "shared-product") -> ExecutionPlan:
    return ExecutionPlan(
        operation="update_listing",
        product_id=product_id,
        title=title,
        bullets=["低延迟", "长续航"],
        price=199,
        stock=800,
        coupon=20,
    )


@pytest.fixture(autouse=True)
def clean_all_execution_partitions():
    SELLER_CENTER_STORE.clear_all()
    BrowserTicketStore.clear_all()
    IdempotencyStore(namespace="tenant_demo").clear()
    IdempotencyStore(namespace="tenant_beta").clear()
    yield
    SELLER_CENTER_STORE.clear_all()
    BrowserTicketStore.clear_all()
    IdempotencyStore(namespace="tenant_demo").clear()
    IdempotencyStore(namespace="tenant_beta").clear()


def test_same_product_id_is_isolated_between_tenant_stores() -> None:
    with tenant_scope("tenant_demo"):
        SELLER_CENTER_STORE.apply_execution_plan(plan("商户 A 标题"))
    with tenant_scope("tenant_beta"):
        SELLER_CENTER_STORE.apply_execution_plan(plan("商户 B 标题"))

    with tenant_scope("tenant_demo"):
        demo = SELLER_CENTER_STORE.snapshot()
    with tenant_scope("tenant_beta"):
        beta = SELLER_CENTER_STORE.snapshot()

    assert demo["tenant_id"] == "tenant_demo"
    assert beta["tenant_id"] == "tenant_beta"
    assert demo["products"]["shared-product"]["title"] == "商户 A 标题"
    assert beta["products"]["shared-product"]["title"] == "商户 B 标题"


def test_same_idempotency_key_is_independent_across_tenants() -> None:
    with tenant_scope("tenant_demo"):
        demo_first = browser_execute(
            plan("商户 A 标题").model_dump(mode="json"), "same-business-key"
        )
        demo_replay = browser_execute(
            plan("商户 A 标题").model_dump(mode="json"), "same-business-key"
        )
    with tenant_scope("tenant_beta"):
        beta_first = browser_execute(
            plan("商户 B 标题").model_dump(mode="json"), "same-business-key"
        )

    assert demo_first["idempotent_replay"] is False
    assert demo_replay["idempotent_replay"] is True
    assert beta_first["idempotent_replay"] is False
    assert demo_first["tenant_id"] == "tenant_demo"
    assert beta_first["tenant_id"] == "tenant_beta"


def test_reset_clears_only_callers_store_partition() -> None:
    with tenant_scope("tenant_demo"):
        browser_execute(plan("A").model_dump(mode="json"), "reset-key")
    with tenant_scope("tenant_beta"):
        browser_execute(plan("B").model_dump(mode="json"), "reset-key")

    with tenant_scope("tenant_demo"):
        reset_seller_center()
        assert get_seller_center_snapshot()["products"] == {}
    with tenant_scope("tenant_beta"):
        assert "shared-product" in get_seller_center_snapshot()["products"]


def test_execution_ticket_restores_issuing_tenant_across_http_boundary() -> None:
    payload = plan("票据绑定到 A").model_dump(mode="json")
    with tenant_scope("tenant_demo"):
        ticket = BrowserTicketStore.issue(payload, purpose="execute")

    with tenant_scope("tenant_beta"):
        result = execute_ticketed_plan(ticket, ExecutionPlan.model_validate(payload))

    assert result["tenant_id"] == "tenant_demo"
    with tenant_scope("tenant_demo"):
        assert "shared-product" in SELLER_CENTER_STORE.snapshot()["products"]
    with tenant_scope("tenant_beta"):
        assert SELLER_CENTER_STORE.snapshot()["products"] == {}


def test_execute_ticket_cannot_be_reused_for_verification() -> None:
    payload = plan("用途隔离").model_dump(mode="json")
    ticket = BrowserTicketStore.issue(payload, purpose="execute")

    with pytest.raises(BrowserTicketPurposeError, match="purpose mismatch"):
        observed_ticketed_product_state(ticket, "shared-product")


def test_verification_ticket_is_tenant_and_product_bound() -> None:
    payload = plan("验证票据", product_id="product-a").model_dump(mode="json")
    with tenant_scope("tenant_beta"):
        SELLER_CENTER_STORE.apply_execution_plan(ExecutionPlan.model_validate(payload))
        ticket = BrowserTicketStore.issue(payload, purpose="verify")

    with pytest.raises(BrowserTicketMismatchError, match="product"):
        observed_ticketed_product_state(ticket, "product-b")

    observed = observed_ticketed_product_state(ticket, "product-a")
    assert observed["tenant_id"] == "tenant_beta"
    assert observed["product"]["title"] == "验证票据"


def test_browser_artifacts_are_namespaced_by_tenant() -> None:
    with tenant_scope("tenant_demo"):
        demo_path = _artifact_path("same", "execute")
    with tenant_scope("tenant_beta"):
        beta_path = _artifact_path("same", "execute")

    assert demo_path.parent.name == "tenant_demo"
    assert beta_path.parent.name == "tenant_beta"
    assert demo_path.parent != beta_path.parent


def test_seller_center_api_projects_only_authenticated_tenant() -> None:
    with tenant_scope("tenant_demo"):
        SELLER_CENTER_STORE.apply_execution_plan(plan("A"))
    with tenant_scope("tenant_beta"):
        SELLER_CENTER_STORE.apply_execution_plan(plan("B"))

    demo = seller_center_state("Bearer demo-merchant-a")
    beta = seller_center_state("Bearer demo-merchant-b")

    assert demo["tenant_id"] == "tenant_demo"
    assert beta["tenant_id"] == "tenant_beta"
    assert demo["products"]["shared-product"]["title"] == "A"
    assert beta["products"]["shared-product"]["title"] == "B"


def test_viewer_can_read_but_cannot_reset_store() -> None:
    assert seller_center_state("Bearer demo-viewer")["tenant_id"] == "tenant_demo"

    with pytest.raises(HTTPException) as denied:
        seller_center_reset("Bearer demo-viewer")
    assert denied.value.status_code == 403
    assert "role_action_not_allowed" in str(denied.value.detail)


def test_execution_status_contains_no_other_tenant_inventory() -> None:
    with tenant_scope("tenant_beta"):
        SELLER_CENTER_STORE.apply_execution_plan(plan("B"))

    status = execution_isolation_status("Bearer demo-merchant-a")

    assert status["tenant_id"] == "tenant_demo"
    assert status["seller_center"]["product_count"] == 0
    assert status["seller_center"]["other_tenant_ids_exposed"] is False
    assert status["idempotency"]["namespace"] == "tenant_demo"
    assert Path(status["browser_artifacts"]["path"]).name == "tenant_demo"
