from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.agents.supervisor as supervisor_module
from app.config import TRACE_DIR
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.policy import LlmPolicy
from app.model.tool_calling import ModelToolCall


def tool_call(call_id: str, name: str, **arguments) -> ModelToolCall:
    return ModelToolCall(
        call_id=call_id,
        name=name,
        arguments=arguments,
        raw_arguments=json.dumps(arguments),
    )


def response(
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
        input_tokens=20,
        output_tokens=10,
        total_tokens=30,
        usage_source="actual",
        prompt_tokens_estimate=20,
        completion_tokens_estimate=10,
        structured_output_mode="tool_calling",
    )


class FixtureAdapter(ModelAdapter):
    def __init__(self, responses: list[ModelResponse]) -> None:
        super().__init__(provider="deepseek", model="deepseek-v4-pro", api_key="fixture")
        self.responses = list(responses)
        self.requests: list[dict] = []

    def complete_with_tools(self, messages, tools, tool_choice="auto") -> ModelResponse:
        self.requests.append(
            {
                "roles": [message["role"] for message in messages],
                "tools": [tool.name for tool in tools],
                "tool_choice": tool_choice,
            }
        )
        return self.responses.pop(0)


def main() -> None:
    final = json.dumps(
        {
            "launch_plan": "首月面向大学生分阶段投放，先小批验证再逐步扩量。",
            "rationale": "毛利率与库存均已通过只读工具验证。",
        },
        ensure_ascii=False,
    )
    calls = [
        tool_call(
            "call_discount",
            "suggest_discount",
            price=199,
            cost=95,
            min_margin_rate=0.25,
        ),
        tool_call(
            "call_margin",
            "calculate_margin",
            price=199,
            cost=95,
            discount=20,
        ),
        tool_call(
            "call_inventory",
            "check_inventory",
            inventory=800,
            planned_units=300,
        ),
    ]
    adapter = FixtureAdapter(
        [
            response("model_early", text=final),
            response("model_tools", calls=calls),
            response("model_final", text=final),
        ]
    )
    policy = LlmPolicy(
        enabled_agents={"strategy_agent"},
        react_enabled_agents={"strategy_agent"},
        fallback_mode="fail_closed",
        max_calls_per_agent=3,
        react_max_steps=3,
        react_max_tool_calls=6,
    )
    supervisor_module.ModelAdapter = lambda **_kwargs: adapter
    supervisor_module.load_llm_policy = lambda: policy
    state = supervisor_module.Supervisor().run(
        "我要上架一款成本95元、售价199元、库存800件的无线耳机，毛利率不低于25%。"
    )

    strategy = state.agent_outputs["strategy_agent"]
    strategy_models = [
        record
        for record in state.model_records
        if record.get("agent_name") == "strategy_agent"
    ]
    strategy_tools = [
        record
        for record in state.tool_records
        if record.get("agent_name") == "strategy_agent"
    ]
    trace_path = TRACE_DIR / f"{state.run_id}.jsonl"
    trace_events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    react_events = [event for event in trace_events if event["event_type"] == "react_step"]
    policy_events = [
        event for event in trace_events if event["event_type"] == "policy_decision"
    ]
    checks = {
        "full_workflow_reached_review": state.status == "waiting_for_approval",
        "strategy_used_react_mode": strategy["generation_mode"] == "react",
        "model_chose_three_tools": [record["tool_name"] for record in strategy_tools]
        == ["suggest_discount", "calculate_margin", "check_inventory"],
        "early_answer_was_not_accepted": react_events[0]["status"]
        == "incomplete_final",
        "required_evidence_was_collected": strategy["margin"]["margin_rate"] == 0.4693
        and strategy["inventory_check"]["valid"] is True,
        "model_continued_after_observation": len(strategy_models) == 3
        and adapter.requests[-1]["roles"][-3:] == ["tool", "tool", "tool"],
        "bounded_loop_stopped_normally": [event["status"] for event in react_events]
        == ["incomplete_final", "tool_calls", "completed"],
        "all_model_tools_were_policy_allowed": len(policy_events) == 3
        and all(event["status"] == "allowed" for event in policy_events),
        "no_model_fallback": not state.model_fallbacks,
        "browser_side_effect_still_waits_for_approval": state.nodes["browser"].status.value
        == "skipped",
    }
    report = {
        "version": "v19",
        "passed": all(checks.values()),
        "evidence_mode": "offline_full_workflow_react_fixture",
        "live_model_called": False,
        "task_id": state.task_id,
        "run_id": state.run_id,
        "model_turns": len(strategy_models),
        "model_selected_tool_calls": len(strategy_tools),
        "react_steps": len(react_events),
        "checks": checks,
        "boundary": (
            "The full DAG and real governed tools ran, but model choices came from an "
            "offline provider fixture. Live DeepSeek quality is not claimed."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
