from __future__ import annotations

from app.access.models import AccessPrincipal, default_principal


class AuthenticationError(PermissionError):
    pass


_DEMO_IDENTITIES = {
    "demo-merchant-a": AccessPrincipal(
        subject_id="demo-merchant-a",
        tenant_id="tenant_demo",
        roles=("operator", "approver"),
    ),
    "demo-merchant-b": AccessPrincipal(
        subject_id="demo-merchant-b",
        tenant_id="tenant_beta",
        roles=("operator", "approver"),
    ),
    "demo-viewer": AccessPrincipal(
        subject_id="demo-viewer",
        tenant_id="tenant_demo",
        roles=("viewer",),
    ),
}


def resolve_principal(authorization: str | None) -> AccessPrincipal:
    if authorization is None or not authorization.strip():
        return default_principal()
    scheme, separator, credential = authorization.strip().partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not credential:
        raise AuthenticationError("Expected an Authorization: Bearer <demo-token> header")
    principal = _DEMO_IDENTITIES.get(credential)
    if principal is None:
        raise AuthenticationError("Unknown or expired demo identity token")
    return principal


def identity_catalog() -> list[dict[str, object]]:
    return [
        {
            "demo_token": token,
            **principal.model_dump(mode="json"),
        }
        for token, principal in _DEMO_IDENTITIES.items()
    ]
