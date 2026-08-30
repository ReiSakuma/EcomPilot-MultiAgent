from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.model.adapter import ModelAdapter, ModelResponseError
from app.model.tool_calling import (
    ToolArgumentsError,
    ToolCallingProtocolError,
    ToolConversation,
    ToolDefinition,
    UnknownToolCallError,
)


class MarginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost: float = Field(gt=0)
    price: float = Field(gt=0)


class InventoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)


MARGIN_TOOL = ToolDefinition(
    name="calculate_margin",
    description="Calculate product gross margin",
    input_model=MarginInput,
)
INVENTORY_TOOL = ToolDefinition(
    name="query_inventory",
    description="Read available inventory for one SKU",
    input_model=InventoryInput,
)


def tool_body(*calls: dict, finish_reason: str = "tool_calls") -> dict:
    return {
        "id": "chatcmpl_tools",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(calls),
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    }


def function_call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class QueuedAdapter(ModelAdapter):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__(provider="deepseek", model="deepseek-v4-pro", api_key="test")
        self.responses = list(responses)
        self.requests: list[tuple[str, dict]] = []

    def _post_json(self, endpoint: str, payload: dict) -> tuple[dict, int]:
        self.requests.append((endpoint, payload))
        return self.responses.pop(0), 1


def test_tool_definition_exports_json_schema_and_validates_arguments() -> None:
    api_tool = MARGIN_TOOL.to_api()

    assert api_tool["type"] == "function"
    assert api_tool["function"]["name"] == "calculate_margin"
    assert set(api_tool["function"]["parameters"]["required"]) == {"cost", "price"}
    assert MARGIN_TOOL.validate_arguments('{"cost":95,"price":199}') == {
        "cost": 95.0,
        "price": 199.0,
    }


def test_deepseek_parses_parallel_tool_calls_and_sends_tool_contracts() -> None:
    adapter = QueuedAdapter(
        [
            tool_body(
                function_call("call_margin", "calculate_margin", '{"cost":95,"price":199}'),
                function_call("call_stock", "query_inventory", '{"sku":"earbud-01"}'),
            )
        ]
    )

    response = adapter.complete_with_tools(
        [{"role": "user", "content": "检查利润和库存"}],
        [MARGIN_TOOL, INVENTORY_TOOL],
    )

    endpoint, payload = adapter.requests[0]
    assert endpoint == "/chat/completions"
    assert payload["tool_choice"] == "auto"
    assert payload["thinking"] == {"type": "disabled"}
    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "calculate_margin",
        "query_inventory",
    ]
    assert [call.name for call in response.tool_calls] == [
        "calculate_margin",
        "query_inventory",
    ]
    assert response.tool_calls[0].arguments == {"cost": 95.0, "price": 199.0}
    assert response.finish_reason == "tool_calls"
    assert response.structured_output_mode == "tool_calling"


def test_tool_results_round_trip_back_to_model_for_final_answer() -> None:
    adapter = QueuedAdapter(
        [
            tool_body(
                function_call("call_margin", "calculate_margin", '{"cost":95,"price":199}'),
                function_call("call_stock", "query_inventory", '{"sku":"earbud-01"}'),
            ),
            {
                "id": "chatcmpl_final",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "利润和库存均满足要求。"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 60, "completion_tokens": 12},
            },
        ]
    )
    conversation = ToolConversation.from_user("检查利润和库存")
    first = adapter.complete_with_tools(
        conversation.messages, [MARGIN_TOOL, INVENTORY_TOOL]
    )
    assert first.assistant_message is not None
    conversation.add_assistant(first.assistant_message)
    conversation.add_tool_result("call_stock", {"available": 800})
    conversation.add_tool_result("call_margin", {"margin_rate": 0.5226})

    final = adapter.complete_with_tools(
        conversation.messages, [MARGIN_TOOL, INVENTORY_TOOL]
    )

    assert final.text == "利润和库存均满足要求。"
    assert final.tool_calls == []
    sent_messages = adapter.requests[1][1]["messages"]
    assert [message["role"] for message in sent_messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert sent_messages[2]["tool_call_id"] == "call_stock"
    assert json.loads(sent_messages[3]["content"]) == {"margin_rate": 0.5226}


@pytest.mark.parametrize(
    ("arguments", "error_match"),
    [
        ("not-json", "not valid JSON"),
        ('{"cost":95}', "failed schema validation"),
        ('{"cost":95,"price":199,"secret":true}', "failed schema validation"),
    ],
)
def test_invalid_tool_arguments_fail_before_execution(
    arguments: str, error_match: str
) -> None:
    adapter = QueuedAdapter(
        [tool_body(function_call("call_bad", "calculate_margin", arguments))]
    )

    with pytest.raises(ToolArgumentsError, match=error_match):
        adapter.complete_with_tools(
            [{"role": "user", "content": "计算毛利"}], [MARGIN_TOOL]
        )


def test_unknown_tool_name_is_rejected() -> None:
    adapter = QueuedAdapter(
        [tool_body(function_call("call_shell", "run_shell", '{"command":"id"}'))]
    )

    with pytest.raises(UnknownToolCallError, match="unknown tool 'run_shell'"):
        adapter.complete_with_tools(
            [{"role": "user", "content": "执行命令"}], [MARGIN_TOOL]
        )


def test_duplicate_call_ids_and_finish_reason_mismatch_are_rejected() -> None:
    duplicate = QueuedAdapter(
        [
            tool_body(
                function_call("same", "calculate_margin", '{"cost":95,"price":199}'),
                function_call("same", "calculate_margin", '{"cost":95,"price":200}'),
            )
        ]
    )
    with pytest.raises(ModelResponseError, match="Duplicate"):
        duplicate.complete_with_tools(
            [{"role": "user", "content": "计算"}], [MARGIN_TOOL]
        )

    wrong_finish_reason = QueuedAdapter(
        [
            tool_body(
                function_call("call_1", "calculate_margin", '{"cost":95,"price":199}'),
                finish_reason="stop",
            )
        ]
    )
    with pytest.raises(ModelResponseError, match="without finish_reason"):
        wrong_finish_reason.complete_with_tools(
            [{"role": "user", "content": "计算"}], [MARGIN_TOOL]
        )


def test_conversation_blocks_next_model_turn_until_all_results_arrive() -> None:
    conversation = ToolConversation.from_user("检查")
    conversation.add_assistant(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                function_call("call_margin", "calculate_margin", '{}'),
                function_call("call_stock", "query_inventory", '{}'),
            ],
        }
    )
    conversation.add_tool_result("call_margin", {"margin_rate": 0.5})

    with pytest.raises(ToolCallingProtocolError, match="call_stock"):
        conversation.ensure_ready_for_model()
    with pytest.raises(ToolCallingProtocolError, match="unknown or completed"):
        conversation.add_tool_result("call_margin", {"margin_rate": 0.5})


def test_tool_call_observer_records_names_without_arguments() -> None:
    events: list[dict] = []
    adapter = QueuedAdapter(
        [tool_body(function_call("call_1", "query_inventory", '{"sku":"private"}'))]
    )
    adapter.set_observer(events.append)

    adapter.complete_with_tools(
        [{"role": "user", "content": "查询"}], [INVENTORY_TOOL]
    )

    details = events[0]["details"]
    assert details["tool_call_count"] == 1
    assert details["tool_names"] == ["query_inventory"]
    assert "private" not in json.dumps(details)


def test_second_turn_trace_redacts_tool_arguments_and_results() -> None:
    events: list[dict] = []
    adapter = QueuedAdapter(
        [
            {
                "id": "chatcmpl_final",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "完成"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 2},
            }
        ]
    )
    adapter.set_observer(events.append)
    conversation = ToolConversation.from_user("查询")
    conversation.add_assistant(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                function_call("call_private", "query_inventory", '{"sku":"secret-sku"}')
            ],
        }
    )
    conversation.add_tool_result("call_private", {"private_stock": 800})

    adapter.complete_with_tools(conversation.messages, [INVENTORY_TOOL])

    serialized = json.dumps(events[0]["details"])
    assert "secret-sku" not in serialized
    assert "private_stock" not in serialized
    assert events[0]["details"]["prompt_preview"] == (
        '{"message_count": 3, "roles": ["user", "assistant", "tool"], '
        '"tool_calling": true}'
    )
