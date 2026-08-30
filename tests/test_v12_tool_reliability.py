from pathlib import Path

import pytest

from app.eval.tool_reliability import run_tool_reliability_eval
from app.orchestration.workflow import run_workflow
from app.safety.idempotency import IdempotencyConflictError, IdempotencyStore
from app.safety.permissions import ToolApprovalRequiredError, ToolPermissionError
from app.tools.registry import ToolRegistry
from app.tools.contracts import EmptyInput
from app.tools.schemas import ToolParameterError, ToolSpec, ToolTimeoutError


def test_v12_workflow_records_validated_tool_contracts():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
        "库存 800 件，毛利率不能低于 25%。",
        approved=True,
        approved_by="qa_operator",
        approval_reason="v12 reliability test",
    )

    assert state.status == "completed"
    assert all(record["validation_status"] == "result_validated" for record in state.tool_records)
    assert {record["agent_name"] for record in state.tool_records} == {
        "market_agent",
        "strategy_agent",
        "browser_agent",
    }
    side_effect = next(record for record in state.tool_records if record["side_effect"])
    assert side_effect["approved_by"] == "qa_operator"


def test_v12_permission_matrix_blocks_cross_agent_tool_call():
    registry = ToolRegistry()

    with pytest.raises(ToolPermissionError):
        with registry.agent_scope("listing_agent"):
            registry.call("calculate_margin", price=199, cost=95)

    assert registry.records()[-1].error_type == "ToolPermissionError"


def test_v12_registry_enforces_approval_independently_of_browser_agent():
    registry = ToolRegistry()

    with pytest.raises(ToolApprovalRequiredError):
        with registry.agent_scope("browser_agent", approved=False):
            registry.call("browser_execute", plan={}, idempotency_key="approval:test")

    assert registry.records()[-1].validation_status == "permission_validated"


def test_v12_pydantic_tool_contract_rejects_bad_business_parameters():
    registry = ToolRegistry()

    with pytest.raises(ToolParameterError):
        with registry.agent_scope("strategy_agent"):
            registry.call("calculate_margin", price=100, cost=50, discount=100)

    assert registry.records()[-1].error_type == "ToolParameterError"


def test_v12_idempotency_is_persistent_and_detects_key_conflicts(tmp_path: Path):
    path = tmp_path / "idempotency.json"
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        return {"status": "applied"}

    first_replay, _ = IdempotencyStore(path).execute_once("key", {"price": 199}, operation)
    second_replay, _ = IdempotencyStore(path).execute_once("key", {"price": 199}, operation)

    assert first_replay is False
    assert second_replay is True
    assert calls == 1
    with pytest.raises(IdempotencyConflictError):
        IdempotencyStore(path).execute_once("key", {"price": 299}, operation)


def test_v12_tool_reliability_eval_passes_all_cases(tmp_path: Path):
    report = run_tool_reliability_eval(
        Path("data/eval/v12_tool_reliability_cases.json"),
        report_path=tmp_path / "report.json",
    )

    assert report["total"] == 7
    assert report["pass_rate"] == 1.0


def test_v12_side_effect_timeout_is_not_retried():
    import time

    registry = ToolRegistry()
    attempts = 0

    def slow_side_effect():
        nonlocal attempts
        attempts += 1
        time.sleep(0.02)
        return {"ok": True}

    registry.register(
        ToolSpec(
            name="slow_write",
            side_effect=True,
            requires_approval=True,
            allowed_agents={"test_agent"},
            input_schema="EmptyInput",
            timeout_seconds=0.001,
            max_retries=2,
        ),
        slow_side_effect,
        EmptyInput,
        lambda result: None,
    )

    with pytest.raises(ToolTimeoutError):
        with registry.agent_scope("test_agent", approved=True, approved_by="operator"):
            registry.call("slow_write")

    assert registry.records()[-1].attempt_count == 1
    time.sleep(0.03)
    assert attempts == 1


def test_v12_tools_catalog_exposes_policy_and_contract():
    browser_spec = next(
        item for item in ToolRegistry().describe_tools() if item["name"] == "browser_execute"
    )

    assert browser_spec["risk_level"] == "high"
    assert browser_spec["requires_approval"] is True
    assert browser_spec["side_effect"] is True
    assert browser_spec["input_schema"] == "BrowserExecuteInput"
    assert set(browser_spec["required_args"]) == {"plan", "idempotency_key"}
    assert "properties" in browser_spec["input_contract"]
