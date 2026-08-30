from __future__ import annotations

import json

import pytest

from app.model.adapter import ModelAdapter, ModelResponse
from app.model.tool_calling import ModelToolCall, ToolConversation
from app.orchestration.react_loop import (
    BoundedReactLoop,
    ReactLoopConfig,
    ReactLoopLimitError,
)
from app.safety.permissions import RiskLevel
from app.safety.policy_gateway import (
    AgentPrincipal,
    PolicyBudget,
    PolicyContext,
    ToolPolicyGateway,
)
from app.tools.governed_executor import GovernedToolExecutor
from app.tools.registry import ToolRegistry


def _call(call_id: str = "call_inventory") -> ModelToolCall:
    arguments = {"inventory": 800, "planned_units": 300}
    return ModelToolCall(
        call_id=call_id,
        name="check_inventory",
        arguments=arguments,
        raw_arguments=json.dumps(arguments),
    )


def _response(
    call_id: str,
    *,
    text: str = "",
    calls: list[ModelToolCall] | None = None,
) -> ModelResponse:
    calls = calls or []
    assistant_message = {"role": "assistant", "content": text or None}
    if calls:
        assistant_message["tool_calls"] = [call.to_api() for call in calls]
    return ModelResponse(
        call_id=call_id,
        provider="deepseek",
        model="deepseek-v4-pro",
        text=text,
        tool_calls=calls,
        assistant_message=assistant_message,
        finish_reason="tool_calls" if calls else "stop",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        usage_source="actual",
        prompt_tokens_estimate=10,
        completion_tokens_estimate=5,
        request_input_tokens_estimate=10,
        requested_max_output_tokens=333,
        structured_output_mode="tool_calling",
    )


class BudgetAdapter(ModelAdapter):
    def __init__(self, responses: list[ModelResponse]) -> None:
        super().__init__(
            provider="deepseek", model="deepseek-v4-pro", api_key="fixture"
        )
        self.responses = list(responses)
        self.requests: list[dict] = []

    def complete_with_tools(
        self,
        messages,
        tools,
        tool_choice="auto",
        *,
        max_output_tokens=None,
    ):
        self.requests.append(
            {
                "messages": messages,
                "tool_choice": tool_choice,
                "max_output_tokens": max_output_tokens,
            }
        )
        return self.responses.pop(0)


def _policy() -> PolicyContext:
    return PolicyContext(
        principal=AgentPrincipal(
            agent_name="strategy_agent", task_id="task_context_v62"
        ),
        max_risk_level=RiskLevel.low,
        tool_allowlist=frozenset({"check_inventory"}),
        budget=PolicyBudget(max_total_calls=3, max_side_effect_calls=0),
    )


def _loop(adapter: ModelAdapter, *, max_steps: int = 2) -> BoundedReactLoop:
    return BoundedReactLoop(
        adapter,
        GovernedToolExecutor(ToolRegistry(), ToolPolicyGateway()),
        ReactLoopConfig(
            max_steps=max_steps,
            input_token_budget=900,
            max_output_tokens=333,
            compression_trigger_ratio=0.50,
        ),
    )


def test_long_tool_result_is_replaced_by_legal_rolling_ledger() -> None:
    system_prompt = "SYSTEM_P0: preserve policy and numeric ownership exactly."
    user_prompt = "USER_P1: inventory=800 and planned_units=300."
    adapter = BudgetAdapter(
        [
            _response("model_tools", calls=[_call()]),
            _response("model_final", text='{"status":"ok"}'),
        ]
    )
    loop = _loop(adapter)

    outcome = loop.run(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        policy_context=_policy(),
        required_tools={"check_inventory"},
        project_tool_result=lambda _name, result: {
            **result,
            "oversized_debug_payload": "X" * 12000,
        },
    )

    assert outcome.compression_count == 1
    decision = outcome.context_decisions[1]
    assert decision.tokens_after < decision.tokens_before
    assert decision.tokens_after <= decision.input_budget_tokens
    second_messages = adapter.requests[1]["messages"]
    ToolConversation(second_messages).ensure_ready_for_model()
    assert [item["role"] for item in second_messages] == ["system", "user", "user"]
    assert second_messages[0]["content"] == system_prompt
    assert second_messages[1]["content"] == user_prompt
    serialized = json.dumps(second_messages, ensure_ascii=False)
    assert "react_context_v62" in serialized
    assert "X" * 1000 not in serialized
    assert adapter.requests[1]["max_output_tokens"] == 333


def test_rejected_tool_feedback_is_compacted_before_retry() -> None:
    adapter = BudgetAdapter(
        [
            _response("model_bad", calls=[_call("call_bad")]),
            _response("model_good", calls=[_call("call_good")]),
            _response("model_final", text='{"status":"ok"}'),
        ]
    )
    loop = _loop(adapter, max_steps=3)

    def reject_first(calls, previous_results):
        if not previous_results and calls[0].call_id == "call_bad":
            raise ValueError("invalid arguments")

    outcome = loop.run(
        system_prompt="system",
        user_prompt="user",
        policy_context=_policy(),
        required_tools={"check_inventory"},
        validate_tool_batch=reject_first,
        tool_error_feedback=lambda _error: {
            "instruction": "correct the arguments",
            "debug": "Y" * 12000,
        },
        max_tool_error_recoveries=1,
    )

    assert [step.action for step in outcome.steps] == [
        "tool_rejected",
        "tool_calls",
        "final",
    ]
    assert outcome.context_decisions[1].compressed is True
    assert outcome.context_decisions[1].tokens_after < outcome.context_decisions[1].tokens_before
    retry_text = json.dumps(adapter.requests[1]["messages"], ensure_ascii=False)
    assert "rejected" in retry_text
    assert "Y" * 1000 not in retry_text


def test_protected_context_overflow_fails_before_model_call() -> None:
    adapter = BudgetAdapter([_response("unused", text="unused")])
    loop = _loop(adapter)

    with pytest.raises(ReactLoopLimitError, match="Protected ReAct"):
        loop.run(
            system_prompt="S" * 5000,
            user_prompt="U" * 5000,
            policy_context=_policy(),
            input_token_budget=300,
        )

    assert adapter.requests == []


def test_real_adapter_sends_per_call_tool_output_budget(monkeypatch) -> None:
    adapter = ModelAdapter(
        provider="deepseek", model="deepseek-v4-pro", api_key="fixture"
    )
    captured: dict = {}

    def fake_post(endpoint, payload):
        captured.update({"endpoint": endpoint, "payload": payload})
        return (
            {
                "id": "response_v62",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            },
            1,
        )

    monkeypatch.setattr(adapter, "_post_json", fake_post)
    executor = GovernedToolExecutor(ToolRegistry(), ToolPolicyGateway())
    definitions = executor.definitions_for(_policy())
    conversation = ToolConversation.from_user("check")

    response = adapter.complete_with_tools(
        conversation.messages,
        definitions,
        max_output_tokens=321,
    )

    assert captured["payload"]["max_tokens"] == 321
    assert response.requested_max_output_tokens == 321
