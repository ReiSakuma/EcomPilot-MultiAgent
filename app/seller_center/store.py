from __future__ import annotations

from threading import RLock

from app.access.context import current_tenant_id
from app.seller_center.schemas import ExecutionPlan, ExecutionVerification, ProductDraft, Promotion


class SellerCenterStore:
    def __init__(self) -> None:
        self.products: dict[str, ProductDraft] = {}
        self.promotions: dict[str, Promotion] = {}
        self.last_execution: dict[str, object] | None = None

    def reset(self) -> None:
        self.products.clear()
        self.promotions.clear()
        self.last_execution = None

    def apply_execution_plan(self, plan: ExecutionPlan) -> dict[str, object]:
        if plan.operation == "update_listing":
            if plan.title is None or plan.price is None or plan.stock is None:
                raise ValueError("update_listing requires title, price, and stock")
            self.products[plan.product_id] = ProductDraft(
                product_id=plan.product_id,
                title=plan.title,
                price=plan.price,
                stock=plan.stock,
                bullets=plan.bullets,
                coupon=plan.coupon,
            )
            if plan.coupon > 0:
                self.promotions[f"coupon_{plan.product_id}"] = Promotion(
                    promotion_id=f"coupon_{plan.product_id}",
                    product_id=plan.product_id,
                    coupon=plan.coupon,
                    status="active",
                )
            self._record_execution(plan)
            return {"status": "applied", "product_id": plan.product_id, "operation": plan.operation}

        if plan.operation == "create_coupon":
            self.promotions[f"coupon_{plan.product_id}"] = Promotion(
                promotion_id=f"coupon_{plan.product_id}",
                product_id=plan.product_id,
                coupon=plan.coupon,
                status="active",
            )
            self._record_execution(plan)
            return {"status": "applied", "product_id": plan.product_id, "operation": plan.operation}

        if plan.operation == "publish_listing":
            product = self.products.get(plan.product_id)
            if product is None:
                raise ValueError(f"Cannot publish missing product: {plan.product_id}")
            self.products[plan.product_id] = product.model_copy(update={"status": "published"})
            self._record_execution(plan)
            return {"status": "applied", "product_id": plan.product_id, "operation": plan.operation}

        raise ValueError(f"Unsupported operation: {plan.operation}")

    def _record_execution(self, plan: ExecutionPlan) -> None:
        self.last_execution = {
            "task_id": plan.task_id,
            "run_id": plan.run_id,
            "checkpoint_version": plan.checkpoint_version,
            "payload_hash": plan.payload_hash,
            "product_id": plan.product_id,
            "operation": plan.operation,
        }

    def verify_execution_plan(self, plan: ExecutionPlan) -> ExecutionVerification:
        product = self.products.get(plan.product_id)
        promotion = self.promotions.get(f"coupon_{plan.product_id}")
        checks: dict[str, bool] = {}
        errors: list[str] = []

        if plan.operation in {"update_listing", "publish_listing"}:
            checks["product_exists"] = product is not None
            if product is None:
                errors.append("product_missing")
                return ExecutionVerification(verified=False, checks=checks, observed={}, errors=errors)
            if plan.title is not None:
                checks["title_match"] = product.title == plan.title
            if plan.price is not None:
                checks["price_match"] = product.price == plan.price
            if plan.stock is not None:
                checks["stock_match"] = product.stock == plan.stock
            if plan.bullets:
                checks["bullets_match"] = product.bullets == plan.bullets
            checks["coupon_match"] = product.coupon == plan.coupon
            if plan.operation == "publish_listing":
                checks["status_published"] = product.status == "published"

        if plan.coupon > 0:
            checks["promotion_exists"] = promotion is not None
            checks["promotion_coupon_match"] = promotion is not None and promotion.coupon == plan.coupon

        for name, ok in checks.items():
            if not ok:
                errors.append(name)
        return ExecutionVerification(
            verified=all(checks.values()) if checks else False,
            checks=checks,
            observed={
                "product": product.model_dump(mode="json") if product else None,
                "promotion": promotion.model_dump(mode="json") if promotion else None,
            },
            errors=errors,
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "products": {
                product_id: product.model_dump(mode="json")
                for product_id, product in self.products.items()
            },
            "promotions": {
                promotion_id: promotion.model_dump(mode="json")
                for promotion_id, promotion in self.promotions.items()
            },
            "last_execution": self.last_execution,
        }


class TenantSellerCenterStore:
    """Routes every store operation to the trusted current tenant partition."""

    def __init__(self) -> None:
        self._stores: dict[str, SellerCenterStore] = {}
        self._lock = RLock()

    def apply_execution_plan(
        self, plan: ExecutionPlan, *, tenant_id: str | None = None
    ) -> dict[str, object]:
        effective_tenant = tenant_id or current_tenant_id()
        with self._lock:
            result = self._store(effective_tenant).apply_execution_plan(plan)
        return {**result, "tenant_id": effective_tenant}

    def verify_execution_plan(
        self, plan: ExecutionPlan, *, tenant_id: str | None = None
    ) -> ExecutionVerification:
        effective_tenant = tenant_id or current_tenant_id()
        with self._lock:
            return self._store(effective_tenant).verify_execution_plan(plan)

    def snapshot(self, *, tenant_id: str | None = None) -> dict[str, object]:
        effective_tenant = tenant_id or current_tenant_id()
        with self._lock:
            snapshot = self._store(effective_tenant).snapshot()
        return {"tenant_id": effective_tenant, **snapshot}

    def reset(self, *, tenant_id: str | None = None) -> None:
        effective_tenant = tenant_id or current_tenant_id()
        with self._lock:
            self._stores.pop(effective_tenant, None)

    def clear_all(self) -> None:
        with self._lock:
            self._stores.clear()

    def partition_status(self, *, tenant_id: str | None = None) -> dict[str, object]:
        effective_tenant = tenant_id or current_tenant_id()
        snapshot = self.snapshot(tenant_id=effective_tenant)
        return {
            "tenant_id": effective_tenant,
            "storage": "process_local_partitioned_memory",
            "product_count": len(snapshot["products"]),
            "promotion_count": len(snapshot["promotions"]),
            "other_tenant_ids_exposed": False,
        }

    def _store(self, tenant_id: str) -> SellerCenterStore:
        store = self._stores.get(tenant_id)
        if store is None:
            store = SellerCenterStore()
            self._stores[tenant_id] = store
        return store


SELLER_CENTER_STORE = TenantSellerCenterStore()
