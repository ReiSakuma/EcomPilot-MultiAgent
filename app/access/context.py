from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar


_TENANT_ID: ContextVar[str] = ContextVar("ecompilot_tenant_id", default="tenant_demo")


def current_tenant_id() -> str:
    return _TENANT_ID.get()


@contextmanager
def tenant_scope(tenant_id: str):
    token = _TENANT_ID.set(tenant_id)
    try:
        yield
    finally:
        _TENANT_ID.reset(token)
