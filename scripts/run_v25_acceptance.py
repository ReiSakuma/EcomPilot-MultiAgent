from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.access.context import tenant_scope
from app.access.identity import resolve_principal
from app.agents.supervisor import Supervisor
from app.browser.backends import _artifact_path
from app.browser.service import execute_ticketed_plan, observed_ticketed_product_state
from app.browser.tickets import BrowserTicketPurposeError, BrowserTicketStore
from app.main import execution_isolation_status, seller_center_state
from app.safety.idempotency import IdempotencyStore
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.store import SELLER_CENTER_STORE
from app.tools.browser_tools import browser_execute, reset_seller_center


GOAL = (
    "我要上架一款成本95元、售价199元、库存800件的无线耳机，"
    "主要面向大学生，毛利率不低于25%。"
)


def plan(title: str) -> ExecutionPlan:
    return ExecutionPlan(
        operation="update_listing",
        product_id="shared-v25-product",
        title=title,
        bullets=["低延迟", "长续航"],
        price=199,
        stock=800,
        coupon=20,
    )


def main() -> None:
    SELLER_CENTER_STORE.clear_all()
    BrowserTicketStore.clear_all()
    for tenant_id in ("tenant_demo", "tenant_beta"):
        IdempotencyStore(namespace=tenant_id).clear()

    demo = resolve_principal("Bearer demo-merchant-a")
    beta = resolve_principal("Bearer demo-merchant-b")
    demo_plan = plan("商户 A 的无线耳机")
    beta_plan = plan("商户 B 的无线耳机")

    with tenant_scope(demo.tenant_id):
        demo_first = browser_execute(
            demo_plan.model_dump(mode="json"), "shared-v25-key"
        )
        demo_replay = browser_execute(
            demo_plan.model_dump(mode="json"), "shared-v25-key"
        )
        demo_snapshot_before_reset = SELLER_CENTER_STORE.snapshot()
        demo_artifact = _artifact_path("same", "execute")
    with tenant_scope(beta.tenant_id):
        beta_first = browser_execute(
            beta_plan.model_dump(mode="json"), "shared-v25-key"
        )
        beta_snapshot = SELLER_CENTER_STORE.snapshot()
        beta_artifact = _artifact_path("same", "execute")

    ticket_payload = demo_plan.model_dump(mode="json")
    with tenant_scope(demo.tenant_id):
        ticket = BrowserTicketStore.issue(ticket_payload, purpose="execute")
    with tenant_scope(beta.tenant_id):
        ticket_result = execute_ticketed_plan(ticket, demo_plan)

    purpose_ticket = BrowserTicketStore.issue(
        ticket_payload, tenant_id=demo.tenant_id, purpose="execute"
    )
    try:
        observed_ticketed_product_state(purpose_ticket, demo_plan.product_id)
    except BrowserTicketPurposeError:
        purpose_mismatch_denied = True
    else:
        purpose_mismatch_denied = False

    with tenant_scope(demo.tenant_id):
        reset_seller_center()
        demo_after_reset = SELLER_CENTER_STORE.snapshot()
    with tenant_scope(beta.tenant_id):
        beta_after_demo_reset = SELLER_CENTER_STORE.snapshot()

    beta_task = Supervisor().run(GOAL, approved=True, principal=beta)
    browser_record = next(
        record
        for record in beta_task.tool_records
        if record["tool_name"] == "browser_execute"
    )
    beta_task_result = beta_task.agent_outputs["browser_agent"]["browser_result"]
    status = execution_isolation_status("Bearer demo-merchant-a")
    beta_api_snapshot = seller_center_state("Bearer demo-merchant-b")

    checks = {
        "same_product_id_has_distinct_tenant_state": demo_snapshot_before_reset[
            "products"
        ][demo_plan.product_id]["title"]
        != beta_snapshot["products"][beta_plan.product_id]["title"],
        "same_idempotency_key_is_independent": demo_first["idempotent_replay"] is False
        and beta_first["idempotent_replay"] is False,
        "same_tenant_idempotency_replays": demo_replay["idempotent_replay"] is True,
        "execution_results_carry_tenant": demo_first["tenant_id"] == demo.tenant_id
        and beta_first["tenant_id"] == beta.tenant_id,
        "ticket_restores_issuing_tenant": ticket_result["tenant_id"] == demo.tenant_id,
        "ticket_purpose_mismatch_is_denied": purpose_mismatch_denied,
        "reset_is_tenant_scoped": demo_after_reset["products"] == {}
        and beta_plan.product_id in beta_after_demo_reset["products"],
        "artifacts_use_tenant_directories": demo_artifact.parent.name == demo.tenant_id
        and beta_artifact.parent.name == beta.tenant_id,
        "full_browser_agent_keeps_tenant_binding": browser_record["tenant_id"] == beta.tenant_id
        and beta_task_result["tenant_id"] == beta.tenant_id,
        "a2a_execution_delegation_keeps_tenant": {
            record.request.tenant_id for record in beta_task.a2a_delegations.values()
        }
        == {beta.tenant_id},
        "execution_status_hides_other_partitions": status["tenant_id"] == demo.tenant_id
        and status["seller_center"]["other_tenant_ids_exposed"] is False,
        "seller_api_projects_authenticated_tenant": beta_api_snapshot["tenant_id"]
        == beta.tenant_id,
    }
    report = {
        "version": "v25",
        "passed": all(checks.values()),
        "task_id": beta_task.task_id,
        "run_id": beta_task.run_id,
        "checks": checks,
        "boundary": (
            "V25 partitions the mock execution surface in one process and binds one-time browser "
            "tickets to tenant, purpose, product and plan. It is not an external commerce account, "
            "distributed transaction system, or infrastructure-level tenant sandbox."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
