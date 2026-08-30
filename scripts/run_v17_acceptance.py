from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model.adapter import ModelAdapter
from app.model.tool_calling import ToolConversation, ToolDefinition


class MarginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost: float = Field(gt=0)
    price: float = Field(gt=0)


class InventoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)


class FixtureDeepSeekAdapter(ModelAdapter):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__(
            provider="deepseek", model="deepseek-v4-pro", api_key="offline-fixture"
        )
        self.responses = responses
        self.requests: list[dict] = []

    def _post_json(self, endpoint: str, payload: dict) -> tuple[dict, int]:
        self.requests.append({"endpoint": endpoint, "payload": payload})
        return self.responses.pop(0), 1


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def main() -> None:
    tools = [
        ToolDefinition(
            "calculate_margin", "Calculate gross margin", MarginInput
        ),
        ToolDefinition(
            "query_inventory", "Read inventory for one SKU", InventoryInput
        ),
    ]
    first_body = {
        "id": "fixture_tool_turn",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        tool_call(
                            "call_margin", "calculate_margin", {"cost": 95, "price": 199}
                        ),
                        tool_call(
                            "call_stock", "query_inventory", {"sku": "wireless-earbud"}
                        ),
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 18},
    }
    final_body = {
        "id": "fixture_final_turn",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "库存充足，按 199 元售价时毛利率约为 52.26%。",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 88, "completion_tokens": 20},
    }
    events: list[dict] = []
    adapter = FixtureDeepSeekAdapter([first_body, final_body])
    adapter.set_observer(events.append)
    conversation = ToolConversation.from_user("检查无线耳机的毛利与库存")

    first = adapter.complete_with_tools(conversation.messages, tools)
    conversation.add_assistant(first.assistant_message or {})
    conversation.add_tool_result("call_margin", {"margin_rate": 0.5226})
    conversation.add_tool_result("call_stock", {"available": 800})
    final = adapter.complete_with_tools(conversation.messages, tools)

    second_messages = adapter.requests[1]["payload"]["messages"]
    checks = {
        "deepseek_chat_completions_endpoint": all(
            request["endpoint"] == "/chat/completions"
            for request in adapter.requests
        ),
        "tool_schemas_sent_to_model": [
            item["function"]["name"]
            for item in adapter.requests[0]["payload"]["tools"]
        ]
        == ["calculate_margin", "query_inventory"],
        "parallel_tool_calls_parsed": len(first.tool_calls) == 2,
        "arguments_locally_validated": first.tool_calls[0].arguments
        == {"cost": 95.0, "price": 199.0},
        "assistant_turn_preserved": second_messages[1]["role"] == "assistant",
        "tool_results_returned": [message["role"] for message in second_messages[-2:]]
        == ["tool", "tool"],
        "model_continued_after_tools": final.finish_reason == "stop"
        and "52.26%" in final.text,
        "trace_contains_tool_metadata": events[0]["details"]["tool_call_count"] == 2
        and events[0]["details"]["tool_names"]
        == ["calculate_margin", "query_inventory"],
    }
    report = {
        "version": "v17",
        "passed": all(checks.values()),
        "evidence_mode": "offline_provider_protocol_fixture",
        "live_model_called": False,
        "model_turns": 2,
        "tool_calls": len(first.tool_calls),
        "checks": checks,
        "boundary": (
            "This validates DeepSeek tool-calling protocol and local argument contracts. "
            "It does not claim autonomous business-tool execution or live-model evidence."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
