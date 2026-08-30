from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.access.identity import resolve_principal
from app.access.policy import AccessDeniedError, AccessPolicy
from app.orchestration.a2a import A2ADelegationRequest
from app.security.capability_tokens import CapabilityAuthorizationError, CapabilityAuthority
from app.security.ledger import SecurityLedger
from app.sql.policy import SqlPolicyDeniedError, SqlPolicyGateway
from app.sql.service import MarketSqlService
from scripts.run_v21_acceptance import run_fixture_task


def main() -> None:
    demo = resolve_principal("Bearer demo-merchant-a")
    beta = resolve_principal("Bearer demo-merchant-b")
    state = run_fixture_task()
    sql_result = state.agent_outputs["market_agent"]["sql_research"]
    sql_tool = next(
        record for record in state.tool_records
        if record["tool_name"] == "query_market_database"
    )

    with tempfile.TemporaryDirectory(prefix="ecompilot-v24-") as directory:
        root = Path(directory)
        service = MarketSqlService(root / "tenant.db")
        query = "SELECT COUNT(*) AS count, ROUND(AVG(price), 2) AS avg_price FROM products"
        demo_rows = service.query(query, tenant_id=demo.tenant_id)["rows"]
        beta_rows = service.query(query, tenant_id=beta.tenant_id)["rows"]
        join_decision = SqlPolicyGateway().authorize(
            "SELECT p.name, r.rating FROM products p JOIN reviews r ON p.id=r.product_id",
            tenant_id=beta.tenant_id,
        )
        try:
            SqlPolicyGateway().authorize(
                "SELECT name FROM products WHERE tenant_id='tenant_beta'",
                tenant_id=demo.tenant_id,
            )
        except SqlPolicyDeniedError:
            tenant_override_denied = True
        else:
            tenant_override_denied = False

        now = datetime.now(timezone.utc)
        request = A2ADelegationRequest(
            task_id="task_v24_acceptance",
            tenant_id=demo.tenant_id,
            sender_agent="supervisor",
            receiver_agent="market_agent",
            capability_id="market.research",
            instruction="tenant-bound query",
            input_state_version=1,
            idempotency_key="v24-tenant-token",
            created_at=now,
            deadline_at=now + timedelta(minutes=1),
        )
        authority = CapabilityAuthority(
            secret=b"v24-acceptance-secret-at-least-32-bytes",
            ledger=SecurityLedger(root / "ledger.jsonl"),
        )
        grant = authority.issue(
            request, allowed_tools=("query_market_database",), max_uses=1
        )
        try:
            authority.verify_and_consume(
                grant.token,
                task_id=request.task_id,
                tenant_id=beta.tenant_id,
                delegation_id=request.delegation_id,
                capability_id=request.capability_id,
                agent_name=request.receiver_agent,
                tool_name="query_market_database",
            )
        except CapabilityAuthorizationError:
            cross_tenant_token_denied = True
        else:
            cross_tenant_token_denied = False

        try:
            AccessPolicy().authorize(
                demo, "task.read", resource_tenant_id=beta.tenant_id
            )
        except AccessDeniedError:
            cross_tenant_read_denied = True
        else:
            cross_tenant_read_denied = False

    delegation_tenants = {
        record.request.tenant_id for record in state.a2a_delegations.values()
    }
    checks = {
        "trusted_identity_is_bound_to_task": state.principal.subject_id == demo.subject_id
        and state.principal.tenant_id == demo.tenant_id,
        "all_a2a_delegations_share_task_tenant": delegation_tenants == {demo.tenant_id},
        "capability_token_is_tenant_bound": sql_tool["tenant_id"] == demo.tenant_id,
        "sql_policy_injected_row_filter": sql_result["policy"]["row_filter_applied"] is True,
        "sql_result_exposes_tenant_evidence": sql_result["tenant_id"] == demo.tenant_id,
        "demo_tenant_retains_frozen_dataset": demo_rows == [{"avg_price": 379.0, "count": 200}],
        "beta_tenant_reads_only_beta_rows": beta_rows == [{"avg_price": 89.0, "count": 2}],
        "join_filters_every_table_alias": "p.tenant_id = 'tenant_beta'" in join_decision.normalized_sql
        and "r.tenant_id = 'tenant_beta'" in join_decision.normalized_sql,
        "model_cannot_override_tenant_filter": tenant_override_denied,
        "cross_tenant_capability_replay_is_denied": cross_tenant_token_denied,
        "cross_tenant_task_read_is_denied": cross_tenant_read_denied,
        "sql_still_runs_in_process_sandbox": sql_result["sandbox"]["isolation"]["separate_process"],
    }
    report = {
        "version": "v24",
        "passed": all(checks.values()),
        "task_id": state.task_id,
        "run_id": state.run_id,
        "tenant_id": state.principal.tenant_id,
        "checks": checks,
        "boundary": (
            "V24 uses static demo bearer identities and AST-enforced SQLite row filters. "
            "It does not claim production OIDC, database-native RLS, or a separate database per tenant."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
