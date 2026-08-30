from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AccessPrincipal(BaseModel):
    """Identity claims created by a trusted API boundary, never by the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    tenant_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    roles: tuple[str, ...] = ()
    authentication_method: str = "demo_bearer"


def default_principal() -> AccessPrincipal:
    return AccessPrincipal(
        subject_id="demo-merchant-a",
        tenant_id="tenant_demo",
        roles=("operator", "approver"),
        authentication_method="demo_default",
    )
