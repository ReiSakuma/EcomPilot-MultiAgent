from __future__ import annotations

from app.access.context import current_tenant_id
from app.browser.backends import get_browser_backend
from app.browser.tickets import BrowserTicketStore
from app.distributed.bulkhead import GLOBAL_BULKHEADS
from app.distributed.runtime import DistributedRuntime
from app.safety.idempotency import IdempotencyStore
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.store import SELLER_CENTER_STORE


SELLER_CENTER_STATE: dict[str, object] = {
    "products": {},
    "promotions": {},
}


def browser_execute(plan: dict[str, object], idempotency_key: str) -> dict[str, object]:
    tenant_id = current_tenant_id()
    store = IdempotencyStore(namespace=tenant_id)
    parsed_plan = ExecutionPlan.model_validate(plan)

    def apply() -> dict[str, object]:
        runtime = DistributedRuntime()
        permit = runtime.prepare_execution(
            tenant_id=tenant_id,
            resource_id=f"product:{parsed_plan.product_id}",
            operation=parsed_plan.operation,
            plan=parsed_plan.model_dump(mode="json"),
            owner_id=idempotency_key,
        )
        if permit.replay_result is not None:
            backend = get_browser_backend()
            # The demo store is intentionally resettable process memory. Rebuild only
            # that local projection; a real seller platform remains the source of truth.
            if backend.name == "mock" and not backend.verify(parsed_plan).get("verified"):
                with GLOBAL_BULKHEADS.acquire("browser"):
                    backend.execute(parsed_plan, permit.idempotency_key)
                SELLER_CENTER_STATE.clear()
                SELLER_CENTER_STATE.update(SELLER_CENTER_STORE.snapshot())
            return {
                **permit.replay_result,
                "execution_replay": True,
                "fencing_token": permit.fencing_token,
            }
        runtime.mark_execution_started(permit)
        try:
            with GLOBAL_BULKHEADS.acquire("browser"):
                apply_result = get_browser_backend().execute(parsed_plan, idempotency_key)
            SELLER_CENTER_STATE.clear()
            SELLER_CENTER_STATE.update(SELLER_CENTER_STORE.snapshot())
            effect, replayed = runtime.confirm_execution(permit, apply_result)
            return {
                **apply_result,
                "execution_replay": replayed,
                "effect_id": effect.effect_id,
                "resource_version": effect.resource_version,
                "fencing_token": effect.fencing_token,
                "saga_id": permit.saga_id,
            }
        except Exception as exc:
            runtime.fail_execution(
                permit,
                str(exc) or type(exc).__name__,
                # A browser error after submission is not assumed to mean no side effect.
                uncertain_external_effect=True,
            )
            raise

    replayed, result = store.execute_once(
        idempotency_key,
        {"tenant_id": tenant_id, "plan": parsed_plan.model_dump(mode="json")},
        apply,
    )
    return {
        "idempotent_replay": replayed,
        "tenant_id": tenant_id,
        "execution_payload_hash": parsed_plan.payload_hash,
        "source_artifact_hashes": parsed_plan.source_artifact_hashes,
        **result,
    }


def browser_verify(plan: dict[str, object]) -> dict[str, object]:
    parsed_plan = ExecutionPlan.model_validate(plan)
    return get_browser_backend().verify(parsed_plan)


def get_seller_center_snapshot() -> dict[str, object]:
    return SELLER_CENTER_STORE.snapshot()


def reset_seller_center() -> dict[str, object]:
    tenant_id = current_tenant_id()
    SELLER_CENTER_STORE.reset()
    BrowserTicketStore.clear(tenant_id=tenant_id)
    IdempotencyStore(namespace=tenant_id).clear()
    DistributedRuntime().reset_tenant(tenant_id)
    SELLER_CENTER_STATE.clear()
    SELLER_CENTER_STATE.update(SELLER_CENTER_STORE.snapshot())
    return SELLER_CENTER_STATE
