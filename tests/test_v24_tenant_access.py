from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.access.context import tenant_scope
from app.access.identity import AuthenticationError, resolve_principal
from app.access.models import AccessPrincipal
from app.access.policy import AccessDeniedError, AccessPolicy
from app.main import TaskRequest, get_task_checkpoint, run_task
from app.orchestration.a2a import A2ADelegationRequest
from app.sql.policy import SqlPolicyDeniedError, SqlPolicyGateway
from app.sql.service import MarketSqlService
from app.security.capability_tokens import (
    CapabilityAuthorizationError,
    CapabilityAuthority,
)
from app.security.ledger import SecurityLedger
from app.tools.registry import ToolRegistry


NOW = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def principal(tenant_id: str, *roles: str) -> AccessPrincipal:
    return AccessPrincipal(
        subject_id=f"user-{tenant_id}",
        tenant_id=tenant_id,
        roles=roles,
    )


def test_demo_identity_is_trusted_and_unknown_token_fails_closed() -> None:
    identity = resolve_principal("Bearer demo-merchant-b")

    assert identity.tenant_id == "tenant_beta"
    assert set(identity.roles) == {"operator", "approver"}
    with pytest.raises(AuthenticationError, match="Unknown"):
        resolve_principal("Bearer attacker-selected-tenant")


def test_rbac_and_tenant_abac_are_both_enforced() -> None:
    policy = AccessPolicy()
    viewer = principal("tenant_demo", "viewer")

    assert policy.authorize(viewer, "task.read").status == "allowed"
    with pytest.raises(AccessDeniedError, match="role_action_not_allowed"):
        policy.authorize(viewer, "task.run")
    with pytest.raises(AccessDeniedError, match="tenant_boundary_mismatch"):
        policy.authorize(viewer, "task.read", resource_tenant_id="tenant_beta")


def test_sql_gateway_injects_unforgeable_filter_for_each_table_alias() -> None:
    decision = SqlPolicyGateway().authorize(
        "SELECT p.name, r.rating FROM products p "
        "JOIN reviews r ON p.id = r.product_id",
        tenant_id="tenant_beta",
    )

    assert decision.row_filter_applied is True
    assert "p.tenant_id = 'tenant_beta'" in decision.normalized_sql
    assert "r.tenant_id = 'tenant_beta'" in decision.normalized_sql
    assert decision.normalized_sql.endswith("LIMIT 50")


def test_model_cannot_read_or_override_hidden_tenant_column() -> None:
    gateway = SqlPolicyGateway()

    with pytest.raises(SqlPolicyDeniedError) as read_error:
        gateway.authorize("SELECT tenant_id FROM products", tenant_id="tenant_demo")
    assert "column_not_allowed" in read_error.value.decision.reason_codes

    with pytest.raises(SqlPolicyDeniedError) as override_error:
        gateway.authorize(
            "SELECT name FROM products WHERE tenant_id = 'tenant_beta'",
            tenant_id="tenant_demo",
        )
    assert "column_not_allowed" in override_error.value.decision.reason_codes


def test_tenant_rows_are_different_inside_same_frozen_database(tmp_path) -> None:
    service = MarketSqlService(tmp_path / "tenant-market.db")
    sql = "SELECT COUNT(*) AS count, ROUND(AVG(price), 2) AS avg_price FROM products"

    demo = service.query(sql, tenant_id="tenant_demo")
    beta = service.query(sql, tenant_id="tenant_beta")

    assert demo["rows"] == [{"avg_price": 379.0, "count": 200}]
    assert beta["rows"] == [{"avg_price": 89.0, "count": 2}]
    assert demo["policy"]["row_filter_applied"] is True
    assert beta["tenant_id"] == "tenant_beta"


def test_tool_thread_preserves_trusted_tenant_context(tmp_path, monkeypatch) -> None:
    service = MarketSqlService(tmp_path / "thread-context.db")
    registry = ToolRegistry()
    monkeypatch.setitem(registry._tools, "query_market_database", service.query)

    with registry.agent_scope(
        "market_agent", task_id="task_tenant", tenant_id="tenant_beta"
    ):
        result = registry.call(
            "query_market_database",
            sql="SELECT COUNT(*) AS count FROM products",
            purpose="tenant_test",
        )

    assert result["rows"] == [{"count": 2}]
    assert registry.records()[-1].tenant_id == "tenant_beta"


def test_capability_token_cannot_cross_tenant_boundary(tmp_path) -> None:
    request = A2ADelegationRequest(
        task_id="task_tenant",
        tenant_id="tenant_demo",
        sender_agent="supervisor",
        receiver_agent="market_agent",
        capability_id="market.research",
        instruction="Query tenant market data",
        input_state_version=1,
        idempotency_key="tenant-capability",
        created_at=NOW,
        deadline_at=NOW + timedelta(minutes=2),
    )
    authority = CapabilityAuthority(
        secret=b"v24-test-secret-that-is-at-least-32-bytes",
        ledger=SecurityLedger(tmp_path / "security.jsonl"),
        clock=lambda: NOW,
    )
    grant = authority.issue(
        request, allowed_tools=("query_market_database",), max_uses=1
    )

    with pytest.raises(CapabilityAuthorizationError, match="tenant_id mismatch"):
        authority.verify_and_consume(
            grant.token,
            task_id=request.task_id,
            tenant_id="tenant_beta",
            delegation_id=request.delegation_id,
            capability_id=request.capability_id,
            agent_name=request.receiver_agent,
            tool_name="query_market_database",
        )


def test_api_denies_viewer_mutation_and_cross_tenant_task_read() -> None:
    goal = "我要上架成本95元、售价199元、库存800件的无线耳机，毛利率不能低于25%。"

    with pytest.raises(HTTPException) as viewer_error:
        run_task(TaskRequest(goal=goal), "Bearer demo-viewer")
    assert viewer_error.value.status_code == 403

    created = run_task(TaskRequest(goal=goal), "Bearer demo-merchant-a")
    task_id = created["task_id"]

    with pytest.raises(HTTPException) as tenant_error:
        get_task_checkpoint(task_id, "Bearer demo-merchant-b")
    assert tenant_error.value.status_code == 403
    assert "tenant_boundary_mismatch" in str(tenant_error.value.detail)


def test_tenant_context_can_be_scoped_without_model_arguments(tmp_path) -> None:
    service = MarketSqlService(tmp_path / "context.db")
    with tenant_scope("tenant_beta"):
        result = service.query("SELECT COUNT(*) AS count FROM products")

    assert result["tenant_id"] == "tenant_beta"
    assert result["rows"] == [{"count": 2}]
