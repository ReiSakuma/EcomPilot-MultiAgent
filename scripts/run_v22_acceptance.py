from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orchestration.a2a import A2ADelegationRequest
from app.security.capability_tokens import (
    CapabilityAuthorizationError,
    CapabilityAuthority,
)
from app.security.ledger import SecurityLedger, build_task_security_summary
from scripts.run_v21_acceptance import run_fixture_task


NOW = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def make_request() -> A2ADelegationRequest:
    return A2ADelegationRequest(
        task_id="task_v22_attack",
        sender_agent="supervisor",
        receiver_agent="market_agent",
        capability_id="market.research",
        instruction="Run governed market research",
        input_state_version=1,
        idempotency_key="v22-acceptance-attack",
        created_at=NOW,
        deadline_at=NOW + timedelta(minutes=1),
    )


def denied(callback) -> bool:
    try:
        callback()
    except CapabilityAuthorizationError:
        return True
    return False


def main() -> None:
    state = run_fixture_task()
    security = build_task_security_summary(state.task_id)
    sql_record = next(
        record
        for record in state.tool_records
        if record["tool_name"] == "query_market_database"
    )

    with tempfile.TemporaryDirectory(prefix="ecompilot-v22-") as directory:
        ledger = SecurityLedger(Path(directory) / "security.jsonl")
        current = [NOW]
        authority = CapabilityAuthority(
            secret=b"v22-acceptance-secret-is-at-least-32-bytes",
            ledger=ledger,
            clock=lambda: current[0],
        )
        request = make_request()
        grant = authority.issue(
            request,
            allowed_tools=("query_market_database",),
            max_uses=1,
        )
        context = dict(
            task_id=request.task_id,
            delegation_id=request.delegation_id,
            capability_id=request.capability_id,
            agent_name=request.receiver_agent,
            tool_name="query_market_database",
        )
        wrong_agent_denied = denied(
            lambda: authority.verify_and_consume(
                grant.token, **{**context, "agent_name": "strategy_agent"}
            )
        )
        wrong_tool_denied = denied(
            lambda: authority.verify_and_consume(
                grant.token, **{**context, "tool_name": "browser_execute"}
            )
        )
        authority.verify_and_consume(grant.token, **context)
        replay_denied = denied(
            lambda: authority.verify_and_consume(grant.token, **context)
        )
        authority.revoke(grant.claims.token_id, reason="acceptance_complete")
        revoked_denied = denied(
            lambda: authority.verify_and_consume(grant.token, **context)
        )
        temporary_integrity = ledger.verify_integrity()

    checks = {
        "each_a2a_delegation_received_a_token": security["summary"]["issued"] == 5,
        "each_token_was_revoked_at_terminal_state": security["summary"]["revoked"] == 5,
        "every_tool_call_was_capability_authorized": security["summary"]["allowed"]
        == len(state.tool_records),
        "tool_records_link_delegation": all(
            record.get("delegation_id") in state.a2a_delegations
            for record in state.tool_records
        ),
        "tool_records_link_capability": all(
            record.get("capability_id") and record.get("capability_token_id")
            for record in state.tool_records
        ),
        "react_text_to_sql_used_same_security_gate": sql_record["capability_id"]
        == "market.research",
        "cross_agent_replay_denied": wrong_agent_denied,
        "out_of_scope_tool_denied": wrong_tool_denied,
        "use_budget_replay_denied": replay_denied,
        "revoked_token_denied": revoked_denied,
        "task_security_ledger_integrity_passed": security["integrity"]["valid"] is True,
        "isolated_attack_ledger_integrity_passed": temporary_integrity["valid"] is True,
    }
    report = {
        "version": "v22",
        "passed": all(checks.values()),
        "task_id": state.task_id,
        "run_id": state.run_id,
        "checks": checks,
        "security_summary": security["summary"],
        "boundary": (
            "HMAC tokens and the SHA-256 ledger are real production paths in one process. "
            "This is not distributed service identity, mTLS, or a hardware-backed key store."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
