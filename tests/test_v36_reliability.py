from __future__ import annotations

from pathlib import Path

import pytest

from app.model.adapter import ModelAdapter, ModelResponse, ModelTransientError
from app.model.policy import LlmPolicy
from app.orchestration.workflow import run_workflow
from app.orchestration.recovery import (
    RecoveryManager,
    RecoveryNotAllowedError,
    RecoveryValidationError,
)
from app.orchestration.state import TaskState
from app.reliability.circuit_breaker import CircuitOpenError
from app.reliability.classifier import build_error_signature, classify_failure
from app.reliability.dead_letter import DeadLetterStore
from app.reliability.models import DeadLetterRecord, FailureTaxonomy, RetryBudget
from app.reliability.policy import retry_decision
from app.tools.contracts import EmptyInput
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolSpec, TransientToolError, UnknownWriteStateError


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("timed out"), FailureTaxonomy.transient),
        (RuntimeError("HTTP 429 rate limit"), FailureTaxonomy.rate_limit),
        (RuntimeError("HTTP 502 bad gateway"), FailureTaxonomy.transient),
        (RuntimeError("invalid JSON schema"), FailureTaxonomy.schema_invalid),
        (PermissionError("403 forbidden"), FailureTaxonomy.permission_denied),
        (RuntimeError("409 version conflict"), FailureTaxonomy.concurrency_conflict),
        (RuntimeError("404 permanent not found"), FailureTaxonomy.permanent),
    ],
)
def test_v36_failure_taxonomy(error: Exception, expected: FailureTaxonomy) -> None:
    assert classify_failure(error) is expected


def test_v36_error_signature_ignores_volatile_ids_and_timestamps() -> None:
    first = build_error_signature(
        RuntimeError("task_abcd1234 failed at 2026-08-28T10:20:30Z after 12345 ms"),
        agent_name="market_agent",
        tool_name="query_market_database",
    )
    second = build_error_signature(
        RuntimeError("task_ffff9999 failed at 2027-09-29T11:21:31Z after 98765 ms"),
        agent_name="market_agent",
        tool_name="query_market_database",
    )
    assert first == second


def test_v36_task_retry_budget_is_shared_and_bounded() -> None:
    budget = RetryBudget(max_attempts=1)
    first = retry_decision(
        component="tool:test",
        category=FailureTaxonomy.transient,
        signature="same",
        attempt=1,
        budget_remaining=budget.remaining,
    )
    assert budget.consume(first) is True
    second = retry_decision(
        component="agent:test",
        category=FailureTaxonomy.transient,
        signature="other",
        attempt=1,
        budget_remaining=budget.remaining,
    )
    assert second.allowed is False
    assert second.reason == "task_retry_budget_exhausted"


def test_v36_transient_tool_retries_and_consumes_task_budget() -> None:
    registry = ToolRegistry()
    calls = 0

    def flaky() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TransientToolError("HTTP 502 temporary")
        return {"ok": True}

    registry.register(
        ToolSpec(name="flaky_read", allowed_agents={"test"}, max_retries=2),
        flaky,
        EmptyInput,
        lambda result: None,
    )
    registry.bind_retry_budget("task_budget", RetryBudget(max_attempts=2))
    with registry.agent_scope("test", task_id="task_budget"):
        assert registry.call("flaky_read") == {"ok": True}
    assert calls == 3
    assert registry.retry_budget("task_budget").consumed == 2
    assert registry.records()[-1].attempt_count == 3


def test_v36_circuit_opens_after_repeated_dependency_failure() -> None:
    registry = ToolRegistry()

    def unavailable() -> None:
        raise TransientToolError("same dependency unavailable")

    registry.register(
        ToolSpec(name="downstream", allowed_agents={"test"}, max_retries=0),
        unavailable,
        EmptyInput,
        lambda result: None,
    )
    with registry.agent_scope("test", task_id="task_circuit"):
        for _ in range(3):
            with pytest.raises(TransientToolError):
                registry.call("downstream")
        with pytest.raises(CircuitOpenError):
            registry.call("downstream")


def test_v36_unknown_write_is_never_blindly_replayed() -> None:
    registry = ToolRegistry()
    calls = 0

    def timeout_write() -> None:
        nonlocal calls
        calls += 1
        raise TimeoutError("write timed out")

    registry.register(
        ToolSpec(
            name="write_once",
            side_effect=True,
            operation_type="write",
            idempotency="keyed",
            compensation="manual",
            reconcile_tool="readback",
            allowed_agents={"test"},
            max_retries=3,
        ),
        timeout_write,
        EmptyInput,
        lambda result: None,
    )
    with registry.agent_scope("test", task_id="task_write"):
        with pytest.raises(UnknownWriteStateError):
            registry.call("write_once")
    assert calls == 1
    assert registry.records()[-1].status == "unknown"


def test_v36_read_only_listing_node_retries_from_checkpoint(monkeypatch) -> None:
    class FlakyListingAdapter(ModelAdapter):
        def __init__(self) -> None:
            super().__init__(
                provider="deepseek", model="deepseek-v4-pro", api_key="fixture"
            )
            self.calls = 0

        def complete(self, prompt, json_schema=None, *, max_output_tokens=None):
            self.calls += 1
            if self.calls == 1:
                error = ModelTransientError("LLM transport failed: TLS disconnected")
                setattr(error, "model_call_id", "model_network_first")
                setattr(error, "model_request_attempts", 2)
                raise error
            return ModelResponse(
                call_id="model_network_recovered",
                provider=self.provider,
                model=self.model,
                text=(
                    '{"title":"游戏耳机","keywords":["游戏耳机"],'
                    '"bullets":["商品卖点仅采用商家已确认的信息"],'
                    '"compliance_notes":[]}'
                ),
                input_tokens=100,
                output_tokens=40,
                total_tokens=140,
                usage_source="test",
                prompt_tokens_estimate=100,
                completion_tokens_estimate=40,
            )

    adapter = FlakyListingAdapter()
    policy = LlmPolicy(
        enabled_agents={"listing_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=2,
    )
    monkeypatch.setattr(
        "app.agents.supervisor.ModelAdapter", lambda **_kwargs: adapter
    )
    monkeypatch.setattr(
        "app.agents.supervisor.load_llm_policy", lambda: policy
    )

    state = run_workflow(
        "我要上架一款成本95元、售价199元、库存800件的游戏耳机。",
        approved=False,
    )

    assert state.status == "waiting_for_approval"
    assert state.failure is None
    assert state.nodes["listing"].retry_count == 1
    assert adapter.calls == 2
    listing_records = [
        record
        for record in state.model_records
        if record.get("agent_name") == "listing_agent"
    ]
    assert [record["status"] for record in listing_records] == [
        "failed",
        "completed",
    ]
    assert listing_records[0]["request_attempts"] == 2


def test_v36_checkpoint_recovery_reuses_verified_read_result() -> None:
    registry = ToolRegistry()
    calls = 0

    def read_once() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"value": calls}

    registry.register(
        ToolSpec(name="recoverable_read", allowed_agents={"test"}),
        read_once,
        EmptyInput,
        lambda result: None,
    )
    with registry.agent_scope("test", task_id="task_reuse"):
        assert registry.call("recoverable_read") == {"value": 1}
    records = [item.model_dump(mode="json") for item in registry.records()]
    registry.seed_recovery_results("task_reuse", records)
    with registry.agent_scope("test", task_id="task_reuse"):
        assert registry.call("recoverable_read") == {"value": 1}
    assert calls == 1
    assert registry.records()[-1].recovered_result is True


def test_v36_unknown_write_requires_readback_before_resume() -> None:
    state = TaskState(goal="recover write")
    state.status = "needs_attention"
    state.tool_records = [
        {"status": "unknown", "side_effect": True, "input_hash": "hash1"}
    ]
    with pytest.raises(RecoveryNotAllowedError):
        RecoveryManager().prepare(state, "run_next", retry_node="market")
    with pytest.raises(RecoveryValidationError):
        RecoveryManager().reconcile_unknown_writes(state, {})
    assert RecoveryManager().reconcile_unknown_writes(state, {"hash1": False}) == ["hash1"]
    assert state.tool_records[0]["status"] == "failed"


def test_v36_dead_letter_is_idempotent_and_tenant_scoped(tmp_path: Path) -> None:
    store = DeadLetterStore(tmp_path / "reliability.db")
    record = DeadLetterRecord(
        task_id="task_dlq",
        tenant_id="tenant_a",
        stage="market",
        category=FailureTaxonomy.unknown,
        error_signature="sig",
        user_message="需要人工处理",
        developer_message="worker crashed",
    )
    assert store.enqueue(record).record_id == record.record_id
    assert store.enqueue(record.model_copy(update={"record_id": "dlq_other"})).record_id == record.record_id
    assert len(store.list(tenant_id="tenant_a")) == 1
    assert store.list(tenant_id="tenant_b") == []


def test_v36_all_tools_publish_complete_lifecycle_contracts() -> None:
    tools = ToolRegistry().describe_tools()
    assert tools
    assert all(item["protocol_version"] == "2.0" for item in tools)
    assert all(item["timeout_seconds"] > 0 for item in tools)
    assert all(item["retryable_errors"] is not None for item in tools)
    write_tools = [item for item in tools if item["operation_type"] == "write"]
    assert [item["name"] for item in write_tools] == ["browser_execute"]
    assert write_tools[0]["idempotency"] == "keyed"
    assert write_tools[0]["reconcile_tool"] == "browser_verify"
