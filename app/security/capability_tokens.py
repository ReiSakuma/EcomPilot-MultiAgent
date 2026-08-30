from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.orchestration.a2a import A2ADelegationRequest
from app.security.ledger import SecurityLedger


class CapabilityAuthorizationError(PermissionError):
    safe_to_retry = False


class CapabilityTokenClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "v1"
    token_id: str = Field(default_factory=lambda: f"cap_{uuid4().hex[:12]}")
    task_id: str
    tenant_id: str = Field(default="tenant_demo", min_length=3, max_length=80)
    delegation_id: str
    capability_id: str
    agent_name: str
    conversation_id: str | None = None
    turn_id: str | None = None
    intent: str = "create_listing"
    access_tier: Literal["read", "write_plan", "write_execute"] = "read"
    approval_granted: bool = False
    allowed_tools: tuple[str, ...] = ()
    state_version: int = Field(ge=0)
    attempt: int = Field(ge=1)
    issued_at: datetime
    expires_at: datetime
    max_uses: int = Field(ge=0, le=100)


class CapabilityGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(repr=False)
    claims: CapabilityTokenClaims


class CapabilityAuthority:
    """Issues and validates short-lived, delegation-bound HMAC capability tokens."""

    def __init__(
        self,
        secret: bytes | None = None,
        ledger: SecurityLedger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._secret = secret or secrets.token_bytes(32)
        if len(self._secret) < 32:
            raise ValueError("Capability HMAC secret must be at least 32 bytes")
        self.ledger = ledger or SecurityLedger()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._revoked: set[str] = set()
        self._uses: dict[str, int] = {}
        self._claims: dict[str, CapabilityTokenClaims] = {}
        self._observer: Callable[[dict[str, Any]], None] | None = None
        self._lock = RLock()

    def set_observer(self, observer: Callable[[dict[str, Any]], None] | None) -> None:
        self._observer = observer

    def issue(
        self,
        request: A2ADelegationRequest,
        *,
        allowed_tools: tuple[str, ...],
        max_uses: int,
    ) -> CapabilityGrant:
        now = self._now()
        if request.deadline_at <= now:
            raise CapabilityAuthorizationError("Cannot issue a token for an expired delegation")
        claims = CapabilityTokenClaims(
            task_id=request.task_id,
            tenant_id=request.tenant_id,
            delegation_id=request.delegation_id,
            capability_id=request.capability_id,
            agent_name=request.receiver_agent,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            intent=request.intent,
            access_tier=request.capability_access,
            approval_granted=request.approval_granted,
            allowed_tools=tuple(sorted(set(allowed_tools))),
            state_version=request.input_state_version,
            attempt=request.attempt,
            issued_at=now,
            expires_at=request.deadline_at,
            max_uses=max_uses,
        )
        payload = claims.model_dump_json().encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        token = f"{_b64encode(payload)}.{_b64encode(signature)}"
        with self._lock:
            self._claims[claims.token_id] = claims
            self._uses[claims.token_id] = 0
        self._audit("token_issued", claims, decision="issued")
        return CapabilityGrant(token=token, claims=claims)

    def verify_and_consume(
        self,
        token: str,
        *,
        task_id: str,
        delegation_id: str,
        capability_id: str,
        agent_name: str,
        tool_name: str,
        tenant_id: str = "tenant_demo",
    ) -> CapabilityTokenClaims:
        claims: CapabilityTokenClaims | None = None
        try:
            claims = self._decode_and_verify(token)
            expected = {
                "task_id": task_id,
                "delegation_id": delegation_id,
                "capability_id": capability_id,
                "agent_name": agent_name,
                "tenant_id": tenant_id,
            }
            for field, value in expected.items():
                if getattr(claims, field) != value:
                    raise CapabilityAuthorizationError(f"Capability token {field} mismatch")
            if tool_name not in claims.allowed_tools:
                raise CapabilityAuthorizationError(
                    f"Tool '{tool_name}' is outside delegated capability '{capability_id}'"
                )
            if claims.access_tier == "write_execute" and not claims.approval_granted:
                raise CapabilityAuthorizationError(
                    "Write-execute capability requires task-bound user approval"
                )
            now = self._now()
            if now < claims.issued_at or now >= claims.expires_at:
                raise CapabilityAuthorizationError("Capability token expired or is not yet valid")
            with self._lock:
                if claims.token_id in self._revoked:
                    raise CapabilityAuthorizationError("Capability token has been revoked")
                if claims.token_id not in self._claims:
                    raise CapabilityAuthorizationError("Capability token was not issued by this runtime")
                uses = self._uses.get(claims.token_id, 0)
                if uses >= claims.max_uses:
                    raise CapabilityAuthorizationError("Capability token use budget exhausted")
                uses += 1
                self._uses[claims.token_id] = uses
            self._audit(
                "tool_allowed", claims, tool_name=tool_name, decision="allowed", use_count=uses
            )
            return claims
        except Exception as exc:
            error = exc if isinstance(exc, CapabilityAuthorizationError) else CapabilityAuthorizationError(str(exc))
            self._audit_denial(
                claims,
                task_id=task_id,
                delegation_id=delegation_id,
                capability_id=capability_id,
                agent_name=agent_name,
                tool_name=tool_name,
                reason=str(error),
            )
            raise error

    def revoke(self, token_id: str, *, reason: str) -> None:
        with self._lock:
            claims = self._claims.get(token_id)
            if claims is None or token_id in self._revoked:
                return
            self._revoked.add(token_id)
            use_count = self._uses.get(token_id, 0)
        self._audit(
            "token_revoked",
            claims,
            decision="revoked",
            reason=reason,
            use_count=use_count,
        )

    def deny(
        self,
        *,
        task_id: str,
        delegation_id: str,
        capability_id: str,
        agent_name: str,
        tool_name: str,
        reason: str,
    ) -> None:
        """Record an authorization failure that occurs before a token can be parsed."""
        self._audit_denial(
            None,
            task_id=task_id,
            delegation_id=delegation_id,
            capability_id=capability_id,
            agent_name=agent_name,
            tool_name=tool_name,
            reason=reason,
        )
        raise CapabilityAuthorizationError(reason)

    def _decode_and_verify(self, token: str) -> CapabilityTokenClaims:
        try:
            payload_part, signature_part = token.split(".", 1)
            payload = _b64decode(payload_part)
            supplied_signature = _b64decode(signature_part)
        except Exception as exc:
            raise CapabilityAuthorizationError("Malformed capability token") from exc
        if _b64encode(payload) != payload_part or _b64encode(supplied_signature) != signature_part:
            raise CapabilityAuthorizationError("Invalid capability token signature encoding")
        expected_signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise CapabilityAuthorizationError("Invalid capability token signature")
        try:
            return CapabilityTokenClaims.model_validate_json(payload)
        except ValidationError as exc:
            raise CapabilityAuthorizationError("Invalid capability token claims") from exc

    def _audit(
        self,
        event_type: str,
        claims: CapabilityTokenClaims,
        *,
        decision: str,
        tool_name: str | None = None,
        reason: str | None = None,
        use_count: int | None = None,
    ) -> None:
        entry = self.ledger.append(
            event_type=event_type,
            token_id=claims.token_id,
            task_id=claims.task_id,
            delegation_id=claims.delegation_id,
            capability_id=claims.capability_id,
            agent_name=claims.agent_name,
            tool_name=tool_name,
            decision=decision,
            reason=reason,
            use_count=use_count,
            max_uses=claims.max_uses,
        )
        self._notify(entry.model_dump(mode="json"))

    def _audit_denial(self, claims: CapabilityTokenClaims | None, **context: Any) -> None:
        payload = {
            "event_type": "tool_denied",
            "token_id": claims.token_id if claims else "unverified",
            "task_id": context["task_id"],
            "delegation_id": context["delegation_id"],
            "capability_id": context["capability_id"],
            "agent_name": context["agent_name"],
            "tool_name": context["tool_name"],
            "decision": "denied",
            "reason": context["reason"],
            "use_count": None,
            "max_uses": claims.max_uses if claims else None,
        }
        entry = self.ledger.append(**payload)
        self._notify(entry.model_dump(mode="json"))

    def _notify(self, details: dict[str, Any]) -> None:
        if self._observer is None:
            return
        try:
            self._observer(
                {
                    "event_type": "capability_token",
                    "component_type": "security",
                    "component_name": "capability_authority",
                    "agent_name": details.get("agent_name"),
                    "step": details["event_type"],
                    "status": details["decision"],
                    "details": details,
                }
            )
        except Exception:
            return

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise CapabilityAuthorizationError("Capability clock must be timezone-aware")
        return value


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)
