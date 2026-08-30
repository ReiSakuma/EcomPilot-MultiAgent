"""Trusted user identity and tenant-aware access control."""

from app.access.models import AccessPrincipal, default_principal
from app.access.policy import AccessDeniedError, AccessPolicy

__all__ = [
    "AccessDeniedError",
    "AccessPolicy",
    "AccessPrincipal",
    "default_principal",
]
