from __future__ import annotations

import secrets
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from threading import RLock
from typing import Any

from app.access.context import current_tenant_id
from app.config import BROWSER_TICKET_TTL_SECONDS
from app.safety.idempotency import fingerprint_payload


class BrowserTicketError(ValueError):
    pass


class BrowserTicketNotFoundError(BrowserTicketError):
    pass


class BrowserTicketExpiredError(BrowserTicketError):
    pass


class BrowserTicketConsumedError(BrowserTicketError):
    pass


class BrowserTicketMismatchError(BrowserTicketError):
    pass


class BrowserTicketPurposeError(BrowserTicketError):
    pass


@dataclass
class BrowserExecutionTicket:
    fingerprint: str
    approved_plan: dict[str, Any]
    tenant_id: str
    purpose: str
    product_id: str
    expires_at: datetime
    used: bool = False


class BrowserTicketStore:
    """Process-local, one-time capability for a single approved execution plan."""

    _lock = RLock()
    _tickets: dict[str, BrowserExecutionTicket] = {}

    @classmethod
    def issue(
        cls,
        plan: dict[str, Any],
        ttl_seconds: int | None = None,
        *,
        tenant_id: str | None = None,
        purpose: str = "execute",
    ) -> str:
        ttl = ttl_seconds or BROWSER_TICKET_TTL_SECONDS
        token = secrets.token_urlsafe(32)
        ticket = BrowserExecutionTicket(
            fingerprint=fingerprint_payload(plan),
            approved_plan=deepcopy(plan),
            tenant_id=tenant_id or current_tenant_id(),
            purpose=purpose,
            product_id=str(plan.get("product_id", "")),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
        )
        with cls._lock:
            cls._cleanup_expired()
            cls._tickets[token] = ticket
        return token

    @classmethod
    def consume(
        cls, token: str, plan: dict[str, Any], *, purpose: str = "execute"
    ) -> BrowserExecutionTicket:
        with cls._lock:
            ticket = cls._require_active(token, purpose=purpose)
            if not compare_digest(ticket.fingerprint, fingerprint_payload(plan)):
                raise BrowserTicketMismatchError("browser execution ticket does not match plan")
            ticket.used = True
            return ticket

    @classmethod
    def consume_editor_plan(
        cls, token: str, plan: dict[str, Any], *, purpose: str = "execute"
    ) -> BrowserExecutionTicket:
        """Consume a ticket after validating only fields exposed by the editor.

        Provenance fields are intentionally not round-tripped through browser DOM
        inputs. The approved server-side plan remains authoritative and is the plan
        that will be executed after this comparison succeeds.
        """
        editable_fields = (
            "operation",
            "product_id",
            "title",
            "price",
            "stock",
            "coupon",
            "bullets",
        )
        submitted = {field: plan.get(field) for field in editable_fields}
        with cls._lock:
            ticket = cls._require_active(token, purpose=purpose)
            approved = {
                field: ticket.approved_plan.get(field) for field in editable_fields
            }
            if not compare_digest(
                fingerprint_payload(approved), fingerprint_payload(submitted)
            ):
                raise BrowserTicketMismatchError(
                    "browser execution ticket does not match editable plan fields"
                )
            ticket.used = True
            return ticket

    @classmethod
    def consume_product(
        cls, token: str, product_id: str, *, purpose: str = "verify"
    ) -> BrowserExecutionTicket:
        with cls._lock:
            ticket = cls._require_active(token, purpose=purpose)
            if not compare_digest(ticket.product_id, product_id):
                raise BrowserTicketMismatchError(
                    "browser verification ticket does not match product"
                )
            ticket.used = True
            return ticket

    @classmethod
    def inspect(
        cls, token: str, *, purpose: str = "execute"
    ) -> BrowserExecutionTicket:
        with cls._lock:
            return cls._require_active(token, purpose=purpose)

    @classmethod
    def clear(cls, *, tenant_id: str | None = None) -> None:
        effective_tenant = tenant_id or current_tenant_id()
        with cls._lock:
            cls._tickets = {
                token: ticket
                for token, ticket in cls._tickets.items()
                if ticket.tenant_id != effective_tenant
            }

    @classmethod
    def clear_all(cls) -> None:
        with cls._lock:
            cls._tickets.clear()

    @classmethod
    def _require_active(
        cls, token: str, *, purpose: str
    ) -> BrowserExecutionTicket:
        ticket = cls._tickets.get(token)
        if ticket is None:
            raise BrowserTicketNotFoundError("browser execution ticket not found")
        if datetime.now(timezone.utc) >= ticket.expires_at:
            cls._tickets.pop(token, None)
            raise BrowserTicketExpiredError("browser execution ticket expired")
        if ticket.used:
            raise BrowserTicketConsumedError("browser execution ticket already consumed")
        if not compare_digest(ticket.purpose, purpose):
            raise BrowserTicketPurposeError(
                f"browser ticket purpose mismatch: expected {purpose}"
            )
        return ticket

    @classmethod
    def _cleanup_expired(cls) -> None:
        now = datetime.now(timezone.utc)
        expired = [token for token, ticket in cls._tickets.items() if now >= ticket.expires_at]
        for token in expired:
            cls._tickets.pop(token, None)
