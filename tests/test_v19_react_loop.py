from __future__ import annotations

import json

import pytest

from app.agents.strategy import StrategyAgent
from app.agents.supervisor import Supervisor
from app.config import TRACE_DIR
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.policy import LlmPolicy, load_llm_policy
from app.model.runtime import get_llm_runtime_status
from app.model.tool_calling import ModelToolCall
from app.orchestration.planner import Planner
from app.orchestration.react_loop import (
    BoundedReactLoop,
    ReactLoopConfig,
    ReactLoopLimitError,
    ReactLoopTimeoutError,
    ReactRepeatedActionError,
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


def tool_call(call_id: str, name: str, **arguments) -> ModelToolCall:
    return ModelToolCall(
        call_id=call_id,
        name=name,
        arguments=arguments,
        raw_arguments=json.dumps(arguments),
    )


def model_response(
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
        structured_output_mode="tool_calling",
    )


class QueuedModelAdapter(ModelAdapter):
    def __init__(self, responses: list[ModelResponse]) -> None:
        super().__init__(provider="deepseek", model="deepseek-v4-pro", api_key="fixture")
        self.responses = list(responses)
        self.requests: list[dict] = []

    def complete_with_tools(self, messages, tools, tool_choice="auto") -> ModelResponse:
        self.requests.append(
            {
                "messages": messages,
                "tools": [tool.name for tool in tools],
                "tool_choice": tool_choice,
            }
        )
        return self.responses.pop(0)


def loop_fixture(
    responses: list[ModelResponse],
    *,
    config: ReactLoopConfig | None = None,
    clock=None,
) -> tuple[BoundedReactLoop, ToolRegistry, ToolPolicyGateway, QueuedModelAdapter]:
    registry = ToolRegistry()
    gateway = ToolPolicyGateway()
    adapter = QueuedModelAdapter(responses)
    loop = BoundedReactLoop(
        adapter,
        GovernedToolExecutor(registry, gateway),
        config=config or ReactLoopConfig(),
        clock=clock,
    )
    return loop, registry, gateway, adapter


def policy_context(max_calls: int = 6) -> PolicyContext:
    return PolicyContext(
        principal=AgentPrincipal(agent_name="strategy_agent", task_id="task_react"),
        max_risk_level=RiskLevel.low,
        tool_allowlist=frozenset(
            {"suggest_discount", "calculate_margin", "check_inventory"}
        ),
        budget=PolicyBudget(
            max_total_calls=max_calls, max_side_effect_calls=0
        ),
    )


FINAL = json.dumps(
    {
        "launch_plan": "首月分阶段投放大学生市场",
        "rationale": "毛利与库存工具结果均满足约束",
        "discount_amount_yuan": 20,
    },
    ensure_ascii=False,
)


def required_calls(prefix: str = "call") -> list[ModelToolCall]:
    return [
        tool_call(
            f"{prefix}_margin",
            "calculate_margin",
            price=199,
            cost=95,
            discount=20,
        ),
        tool_call(
            f"{prefix}_inventory",
            "check_inventory",
            inventory=800,
            planned_units=300,
        ),
    ]


def test_react_loop_executes_parallel_tools_then_returns_final_answer() -> None:
    loop, registry, _, adapter = loop_fixture(
        [
            model_response("model_tools", calls=required_calls()),
            model_response("model_final", text=FINAL),
        ]
    )

    result = loop.run(
        system_prompt="system",
        user_prompt="user",
        policy_context=policy_context(),
        required_tools={"calculate_margin", "check_inventory"},
    )

    assert result.stop_reason == "model_final"
    assert result.tool_call_count == 2
    assert [step.action for step in result.steps] == ["tool_calls", "final"]
    assert result.tool_results["calculate_margin"]["margin_rate"] == 0.4693
    assert len(registry.records()) == 2
    assert [message["role"] for message in adapter.requests[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]


def test_early_final_answer_is_rejected_until_required_evidence_exists() -> None:
    loop, _, _, adapter = loop_fixture(
        [
            model_response("model_early", text=FINAL),
            model_response("model_tools", calls=required_calls()),
            model_response("model_final", text=FINAL),
        ],
        config=ReactLoopConfig(max_steps=3),
    )

    result = loop.run(
        system_prompt="system",
        user_prompt="user",
        policy_context=policy_context(),
        required_tools={"calculate_margin", "check_inventory"},
    )

    assert [step.action for step in result.steps] == [
        "incomplete_final",
        "tool_calls",
        "final",
    ]
    assert "Required tool evidence" in adapter.requests[1]["messages"][-1]["content"]


def test_identical_action_is_blocked_before_second_execution() -> None:
    repeated = tool_call(
        "call_first", "calculate_margin", price=199, cost=95, discount=20
    )
    second = repeated.model_copy(update={"call_id": "call_second"})
    loop, registry, _, _ = loop_fixture(
        [
            model_response("model_1", calls=[repeated]),
            model_response("model_2", calls=[second]),
        ]
    )

    with pytest.raises(ReactRepeatedActionError):
        loop.run(
            system_prompt="system",
            user_prompt="user",
            policy_context=policy_context(),
            required_tools={"calculate_margin", "check_inventory"},
        )

    assert len(registry.records()) == 1


def test_step_limit_stops_loop_without_unbounded_model_calls() -> None:
    loop, registry, _, adapter = loop_fixture(
        [
            model_response(
                "model_1",
                calls=[
                    tool_call(
                        "call_1", "check_inventory", inventory=800, planned_units=100
                    )
                ],
            ),
            model_response(
                "model_2",
                calls=[
                    tool_call(
                        "call_2", "check_inventory", inventory=800, planned_units=200
                    )
                ],
            ),
        ],
        config=ReactLoopConfig(max_steps=2),
    )

    with pytest.raises(ReactLoopLimitError, match="step limit"):
        loop.run(
            system_prompt="system",
            user_prompt="user",
            policy_context=policy_context(),
            required_tools={"calculate_margin"},
        )

    assert len(adapter.requests) == 2
    assert len(registry.records()) == 2


def test_loop_tool_call_budget_blocks_entire_batch() -> None:
    loop, registry, gateway, _ = loop_fixture(
        [model_response("model_tools", calls=required_calls())],
        config=ReactLoopConfig(max_tool_calls=1),
    )

    with pytest.raises(ReactLoopLimitError, match="tool-call budget"):
        loop.run(
            system_prompt="system",
            user_prompt="user",
            policy_context=policy_context(),
        )

    assert registry.records() == []
    assert gateway.usage(policy_context().principal).total_calls == 0


def test_loop_timeout_after_model_response_prevents_tool_execution() -> None:
    times = iter([0.0, 0.0, 2.0])
    loop, registry, _, _ = loop_fixture(
        [model_response("model_tools", calls=required_calls())],
        config=ReactLoopConfig(timeout_seconds=1),
        clock=lambda: next(times),
    )
    events: list[dict] = []
    loop.set_observer(events.append)

    with pytest.raises(ReactLoopTimeoutError):
        loop.run(
            system_prompt="system",
            user_prompt="user",
            policy_context=policy_context(),
        )

    assert registry.records() == []
    assert events[-1]["status"] == "failed"
    assert events[-1]["error"]["type"] == "ReactLoopTimeoutError"


def test_react_trace_records_actions_without_arguments_or_results() -> None:
    loop, _, _, _ = loop_fixture(
        [
            model_response("model_tools", calls=required_calls()),
            model_response("model_final", text=FINAL),
        ]
    )
    events: list[dict] = []
    loop.set_observer(events.append)

    loop.run(
        system_prompt="system",
        user_prompt="user",
        policy_context=policy_context(),
        required_tools={"calculate_margin", "check_inventory"},
    )

    serialized = json.dumps(events, ensure_ascii=False)
    assert [event["status"] for event in events] == ["tool_calls", "completed"]
    assert "calculate_margin" in serialized
    assert "199" not in serialized
    assert "margin_rate" not in serialized


def test_strategy_agent_uses_react_tools_and_structured_final_answer() -> None:
    adapter = QueuedModelAdapter(
        [
            model_response(
                "model_tools",
                calls=[tool_call(
                    "call_forecast",
                    "forecast_demand",
                    category="无线耳机",
                    target_audience="大学生",
                    target_price=199,
                    horizon_days=30,
                )],
            ),
            model_response("model_final", text=FINAL),
        ]
    )
    registry = ToolRegistry()
    gateway = ToolPolicyGateway()
    loop = BoundedReactLoop(
        adapter,
        GovernedToolExecutor(registry, gateway),
        ReactLoopConfig(max_steps=2),
    )
    policy = LlmPolicy(
        enabled_agents={"strategy_agent"},
        react_enabled_agents={"strategy_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=2,
    )
    agent = StrategyAgent(
        registry, model_adapter=adapter, llm_policy=policy, react_loop=loop
    )
    state = Planner().build_initial_state(
        "我要上架一款成本95元、售价199元、库存800件的无线耳机，毛利率不低于25%。"
    )

    handoff = agent.run(state)

    assert handoff.result["generation_mode"] == "react"
    assert handoff.result["coupon"] == 20
    assert handoff.result["inventory_check"]["valid"] is True
    assert len(state.model_records) == 2
    assert state.model_records[-1]["structured_validation"] == "passed"
    assert [record.tool_name for record in registry.records()] == [
        "suggest_discount",
        "forecast_demand",
        "calculate_margin",
        "check_inventory",
    ]


def test_strategy_rejects_model_tool_arguments_that_change_user_constraints() -> None:
    adapter = QueuedModelAdapter(
        [
            model_response(
                "model_bad",
                calls=[
                    tool_call(
                        "call_bad_margin",
                        "calculate_margin",
                        price=999,
                        cost=1,
                        discount=0,
                    )
                ],
            )
        ]
    )
    registry = ToolRegistry()
    loop = BoundedReactLoop(
        adapter, GovernedToolExecutor(registry, ToolPolicyGateway())
    )
    policy = LlmPolicy(
        enabled_agents={"strategy_agent"},
        react_enabled_agents={"strategy_agent"},
        fallback_mode="deterministic",
    )
    agent = StrategyAgent(
        registry, model_adapter=adapter, llm_policy=policy, react_loop=loop
    )
    state = Planner().build_initial_state(
        "我要上架一款成本95元、售价199元、库存800件的无线耳机，毛利率不低于25%。"
    )

    with registry.agent_scope("strategy_agent", task_id=state.task_id):
        handoff = agent.run(state)

    assert handoff.result["generation_mode"] == "deterministic_fallback"
    assert state.model_fallbacks[0]["error_type"] == "PolicyDeniedError"
    assert all(record.args.get("price") != 999 for record in registry.records())


def test_react_feature_flag_is_loaded_separately_from_llm_flag(monkeypatch) -> None:
    monkeypatch.setenv("ECOMPILOT_LLM_AGENTS", "strategy_agent")
    monkeypatch.setenv("ECOMPILOT_REACT_AGENTS", "strategy_agent")
    monkeypatch.setenv("ECOMPILOT_REACT_MAX_STEPS", "3")
    monkeypatch.setenv("ECOMPILOT_REACT_MAX_TOOL_CALLS", "5")

    policy = load_llm_policy()

    assert policy.enabled_for("strategy_agent") is True
    assert policy.react_enabled_for("strategy_agent") is True
    assert policy.react_max_steps == 2
    assert policy.react_max_tool_calls == 2


def test_supervisor_runs_strategy_react_inside_full_workflow(monkeypatch) -> None:
    adapter = QueuedModelAdapter(
        [
            model_response("model_tools", calls=[tool_call(
                "workflow_forecast",
                "forecast_demand",
                category="无线耳机",
                target_audience="大学生",
                target_price=199,
                horizon_days=30,
            )]),
            model_response("model_final", text=FINAL),
        ]
    )
    policy = LlmPolicy(
        enabled_agents={"strategy_agent"},
        react_enabled_agents={"strategy_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=2,
        react_max_steps=2,
    )
    monkeypatch.setattr("app.agents.supervisor.ModelAdapter", lambda **_kwargs: adapter)
    monkeypatch.setattr("app.agents.supervisor.load_llm_policy", lambda: policy)
    supervisor = Supervisor()

    state = supervisor.run(
        "我要上架一款成本95元、售价199元、库存800件的无线耳机，毛利率不低于25%。"
    )

    assert state.agent_outputs["strategy_agent"]["generation_mode"] == "react"
    assert len(
        [
            record
            for record in state.model_records
            if record.get("agent_name") == "strategy_agent"
        ]
    ) == 2
    trace_text = (TRACE_DIR / f"{state.run_id}.jsonl").read_text(encoding="utf-8")
    assert '"event_type":"react_step"' in trace_text
    assert '"event_type":"policy_decision"' in trace_text


def test_runtime_preflight_rejects_unsupported_react_configuration(
    monkeypatch,
) -> None:
    policy = LlmPolicy(
        enabled_agents={"listing_agent"},
        react_enabled_agents={"listing_agent"},
    )
    monkeypatch.setattr("app.model.runtime.load_llm_policy", lambda: policy)
    monkeypatch.setattr("app.model.runtime.LLM_PROVIDER", "openai")

    status = get_llm_runtime_status()

    assert "unsupported_react_agents:listing_agent" in status["issues"]
    assert "react_provider_must_be_deepseek" in status["issues"]
    assert status["ready"] is False
