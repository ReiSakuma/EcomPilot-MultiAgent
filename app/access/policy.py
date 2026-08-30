from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.access.audit import AccessAuditStore
from app.access.models import AccessPrincipal


ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"task.read", "evidence.read", "seller.read"}),
    "operator": frozenset(
        {"task.run", "task.read", "evidence.read", "sql.market.query", "seller.read"}
    ),
    "approver": frozenset({"task.read", "task.approve", "seller.execute"}),
    "admin": frozenset({"*"}),
}


class AccessDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(default_factory=lambda: f"access_{uuid4().hex[:12]}")
    status: Literal["allowed", "denied"]
    subject_id: str
    tenant_id: str
    roles: tuple[str, ...]
    action: str
    resource_tenant_id: str | None = None
    reason_codes: tuple[str, ...] = ()


class AccessDeniedError(PermissionError):
    def __init__(self, decision: AccessDecision) -> None:
        self.decision = decision
        super().__init__(
            f"Access denied for '{decision.subject_id}' on '{decision.action}': "
            + ", ".join(decision.reason_codes)
        )


class AccessPolicy:
    """Combines role permissions (RBAC) with tenant attributes (ABAC)."""

    def __init__(self, audit_store: AccessAuditStore | None = None) -> None:
        self.audit_store = audit_store or AccessAuditStore()

    def authorize(
        self,
        principal: AccessPrincipal,
        action: str,
        *,
        resource_tenant_id: str | None = None,
    ) -> AccessDecision:
        allowed_actions = set().union(
            *(ROLE_ACTIONS.get(role, frozenset()) for role in principal.roles)
        )
        reasons: list[str] = []
        if action not in allowed_actions and "*" not in allowed_actions:
            reasons.append("role_action_not_allowed")
        if resource_tenant_id is not None and resource_tenant_id != principal.tenant_id:
            reasons.append("tenant_boundary_mismatch")
        decision = AccessDecision(
            status="denied" if reasons else "allowed",
            subject_id=principal.subject_id,
            tenant_id=principal.tenant_id,
            roles=principal.roles,
            action=action,
            resource_tenant_id=resource_tenant_id,
            reason_codes=tuple(reasons or ["rbac_abac_allowed"]),
        )
        self.audit_store.append(decision.model_dump(mode="json"))
        if reasons:
            raise AccessDeniedError(decision)
        return decision

    @staticmethod
    def catalog() -> dict[str, object]:
        return {
            "model": "RBAC+ABAC",
            "roles": {role: sorted(actions) for role, actions in ROLE_ACTIONS.items()},
            "tenant_rule": "resource_tenant_id must equal the trusted principal tenant_id",
            "identity_source": "static demo bearer registry",
            "production_boundary": "Replace demo tokens with OIDC/JWT validation and persistent audit storage.",
        }
