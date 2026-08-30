from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from multiprocessing import Process
from pathlib import Path

import pytest

from app.demo_ui import DEMO_HTML
from app.main import task_security_summary, verify_security_ledger
from app.orchestration.a2a import A2ADelegationRequest
from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval
from app.security.capability_tokens import (
    CapabilityAuthorizationError,
    CapabilityAuthority,
)
from app.security.ledger import SecurityLedger, build_task_security_summary
from app.tools.browser_tools import reset_seller_center
from app.tools.registry import ToolRegistry


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，主要面向大学生，"
    "库存800件，毛利率不能低于25%。"
)
NOW = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
SECRET = b"v22-test-secret-that-is-at-least-32-bytes"


def append_security_events(path: str, worker_id: str, count: int) -> None:
    ledger = SecurityLedger(Path(path))
    for index in range(count):
        ledger.append(
            event_type="tool_allowed",
            token_id=f"token-{worker_id}-{index}",
            task_id=f"task-{worker_id}",
            delegation_id=f"delegation-{worker_id}",
            capability_id="market.research",
            agent_name="market_agent",
            tool_name="build_market_report",
            decision="allowed",
        )


def request(**updates) -> A2ADelegationRequest:
    values = {
        "task_id": "task_security",
        "sender_agent": "supervisor",
        "receiver_agent": "strategy_agent",
        "capability_id": "strategy.plan",
        "instruction": "Build a governed strategy",
        "input_state_version": 3,
        "attempt": 1,
        "idempotency_key": "idem-v22-test",
        "created_at": NOW,
        "deadline_at": NOW + timedelta(minutes=2),
    }
    values.update(updates)
    return A2ADelegationRequest(**values)


def authority(tmp_path, current=None) -> CapabilityAuthority:
    current = current or [NOW]
    return CapabilityAuthority(
        secret=SECRET,
        ledger=SecurityLedger(tmp_path / "ledger.jsonl"),
        clock=lambda: current[0],
    )


def strategy_grant(authority: CapabilityAuthority, *, max_uses: int = 2):
    return authority.issue(
        request(),
        allowed_tools=("check_inventory", "calculate_margin"),
        max_uses=max_uses,
    )


def verify_inventory(authority: CapabilityAuthority, token: str, **updates):
    context = {
        "task_id": "task_security",
        "delegation_id": request().delegation_id,
        "capability_id": "strategy.plan",
        "agent_name": "strategy_agent",
        "tool_name": "check_inventory",
    }
    context.update(updates)
    return authority.verify_and_consume(token, **context)


def test_signed_token_accepts_exact_delegation_context(tmp_path) -> None:
    auth = authority(tmp_path)
    req = request()
    grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=1)

    claims = auth.verify_and_consume(
        grant.token,
        task_id=req.task_id,
        delegation_id=req.delegation_id,
        capability_id=req.capability_id,
        agent_name=req.receiver_agent,
        tool_name="check_inventory",
    )

    assert claims.token_id == grant.claims.token_id
    assert auth.ledger.verify_integrity()["valid"] is True


def test_tampered_signature_is_denied_and_audited(tmp_path) -> None:
    auth = authority(tmp_path)
    req = request()
    grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=1)
    tampered = grant.token[:-1] + ("A" if grant.token[-1] != "A" else "B")

    with pytest.raises(CapabilityAuthorizationError, match="signature"):
        auth.verify_and_consume(
            tampered,
            task_id=req.task_id,
            delegation_id=req.delegation_id,
            capability_id=req.capability_id,
            agent_name=req.receiver_agent,
            tool_name="check_inventory",
        )

    assert auth.ledger.read()[-1].event_type == "tool_denied"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "task_other"),
        ("delegation_id", "dlg_other"),
        ("capability_id", "market.research"),
        ("agent_name", "market_agent"),
    ],
)
def test_cross_scope_token_replay_is_denied(tmp_path, field, value) -> None:
    auth = authority(tmp_path)
    req = request()
    grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=2)
    context = {
        "task_id": req.task_id,
        "delegation_id": req.delegation_id,
        "capability_id": req.capability_id,
        "agent_name": req.receiver_agent,
        "tool_name": "check_inventory",
        field: value,
    }

    with pytest.raises(CapabilityAuthorizationError, match="mismatch"):
        auth.verify_and_consume(grant.token, **context)


def test_tool_outside_capability_is_denied(tmp_path) -> None:
    auth = authority(tmp_path)
    req = request()
    grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=1)

    with pytest.raises(CapabilityAuthorizationError, match="outside delegated"):
        auth.verify_and_consume(
            grant.token,
            task_id=req.task_id,
            delegation_id=req.delegation_id,
            capability_id=req.capability_id,
            agent_name=req.receiver_agent,
            tool_name="browser_execute",
        )


def test_expiry_use_budget_and_revocation_fail_closed(tmp_path) -> None:
    current = [NOW]
    auth = authority(tmp_path, current)
    req = request()
    budget_grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=1)
    context = dict(
        task_id=req.task_id,
        delegation_id=req.delegation_id,
        capability_id=req.capability_id,
        agent_name=req.receiver_agent,
        tool_name="check_inventory",
    )
    auth.verify_and_consume(budget_grant.token, **context)
    with pytest.raises(CapabilityAuthorizationError, match="budget"):
        auth.verify_and_consume(budget_grant.token, **context)

    revoke_grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=2)
    auth.revoke(revoke_grant.claims.token_id, reason="delegation_completed")
    with pytest.raises(CapabilityAuthorizationError, match="revoked"):
        auth.verify_and_consume(revoke_grant.token, **context)

    expired_grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=1)
    current[0] = req.deadline_at
    with pytest.raises(CapabilityAuthorizationError, match="expired"):
        auth.verify_and_consume(expired_grant.token, **context)


def test_registry_requires_token_and_records_binding(tmp_path) -> None:
    auth = authority(tmp_path)
    req = request()
    grant = auth.issue(req, allowed_tools=("check_inventory",), max_uses=1)
    registry = ToolRegistry(auth, require_capability_token=True)

    with registry.agent_scope(
        req.receiver_agent,
        task_id=req.task_id,
        delegation_id=req.delegation_id,
        capability_id=req.capability_id,
        capability_token=grant.token,
        capability_token_id=grant.claims.token_id,
    ):
        result = registry.call("check_inventory", inventory=800, planned_units=300)

    record = registry.records()[-1]
    assert result["remaining"] == 500
    assert record.validation_status == "result_validated"
    assert record.delegation_id == req.delegation_id
    assert record.capability_id == req.capability_id
    assert record.capability_token_id == grant.claims.token_id


def test_registry_denies_tool_call_without_delegation_token(tmp_path) -> None:
    auth = authority(tmp_path)
    registry = ToolRegistry(auth, require_capability_token=True)

    with registry.agent_scope("strategy_agent", task_id="task_security"):
        with pytest.raises(CapabilityAuthorizationError, match="required"):
            registry.call("check_inventory", inventory=800, planned_units=300)

    assert auth.ledger.read()[-1].event_type == "tool_denied"
    assert auth.ledger.read()[-1].token_id == "unverified"


def test_hash_chain_detects_persistent_ledger_tampering(tmp_path) -> None:
    auth = authority(tmp_path)
    req = request()
    auth.issue(req, allowed_tools=("check_inventory",), max_uses=1)
    path = auth.ledger.path
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["agent_name"] = "attacker_agent"
    lines[0] = json.dumps(payload)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = auth.ledger.verify_integrity()

    assert result["valid"] is False
    assert result["invalid_index"] == 0


def test_hash_chain_serializes_cross_process_appends(tmp_path) -> None:
    path = tmp_path / "shared-ledger.jsonl"
    workers = [
        Process(target=append_security_events, args=(str(path), str(index), 10))
        for index in range(3)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(worker.exitcode == 0 for worker in workers)
    ledger = SecurityLedger(path)
    assert len(ledger.read()) == 30
    assert ledger.verify_integrity()["valid"] is True


def test_full_workflow_binds_every_tool_call_to_a2a_capability() -> None:
    reset_seller_center()
    state = run_workflow(GOAL, approved=False)

    assert state.status == "waiting_for_approval"
    assert state.tool_records
    assert all(record["delegation_id"] for record in state.tool_records)
    assert all(record["capability_id"] for record in state.tool_records)
    assert all(record["capability_token_id"] for record in state.tool_records)
    assert all(
        record["delegation_id"] in state.a2a_delegations
        for record in state.tool_records
    )
    security = build_task_security_summary(state.task_id)
    assert security["summary"] == {
        "issued": 6,
        "allowed": 4,
        "denied": 0,
        "revoked": 6,
    }


def test_approval_resume_uses_new_token_and_keeps_old_one_revoked() -> None:
    reset_seller_center()
    initial = run_workflow(GOAL, approved=False)
    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(approved=True, approver="v22-security-test"),
        expected_checkpoint_version=initial.checkpoint_version,
    )
    security = build_task_security_summary(resumed.task_id)
    browser_events = [
        event
        for event in security["events"]
        if event["agent_name"] == "browser_agent"
    ]
    issued_ids = {
        event["token_id"] for event in browser_events if event["event_type"] == "token_issued"
    }

    assert resumed.status == "completed"
    assert len(issued_ids) == 2
    assert all(
        any(
            candidate["event_type"] == "token_revoked"
            and candidate["token_id"] == token_id
            for candidate in browser_events
        )
        for token_id in issued_ids
    )


def test_security_api_and_ops_ui_expose_only_sanitized_evidence() -> None:
    reset_seller_center()
    state = run_workflow(GOAL, approved=False)
    projection = task_security_summary(state.task_id)
    serialized = json.dumps(projection, ensure_ascii=False)

    assert projection["integrity"]["valid"] is True
    assert verify_security_ledger()["valid"] is True
    assert "能力票据与安全账本" in DEMO_HTML
    assert "/security" in DEMO_HTML
    assert "capability_token" not in serialized
    assert "signature" not in serialized
    assert "secret" not in serialized
