from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ToolCallingProtocolError(ValueError):
    """Raised when a model/tool message violates the tool-calling contract."""


class UnknownToolCallError(ToolCallingProtocolError):
    pass


class ToolArgumentsError(ToolCallingProtocolError):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    """Model-facing tool metadata backed by a locally enforced Pydantic schema."""

    name: str
    description: str
    input_model: type[BaseModel]

    def __post_init__(self) -> None:
        if not _TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ToolCallingProtocolError(
                "Tool name must contain 1-64 letters, numbers, underscores, or hyphens"
            )
        if not self.description.strip():
            raise ToolCallingProtocolError("Tool description must not be empty")
        if not isinstance(self.input_model, type) or not issubclass(
            self.input_model, BaseModel
        ):
            raise ToolCallingProtocolError("Tool input_model must be a Pydantic model")

    def to_api(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    def validate_arguments(self, raw_arguments: str) -> dict[str, Any]:
        try:
            decoded = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ToolArgumentsError(
                f"Tool '{self.name}' arguments are not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(decoded, dict):
            raise ToolArgumentsError(
                f"Tool '{self.name}' arguments must be a JSON object"
            )
        try:
            validated = self.input_model.model_validate(decoded)
        except ValidationError as exc:
            raise ToolArgumentsError(
                f"Tool '{self.name}' arguments failed schema validation: {exc}"
            ) from exc
        return validated.model_dump(mode="json", exclude_none=True)


class ModelToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments,
            },
        }


class ToolConversation:
    """Builds a valid user -> assistant tool call -> tool result history."""

    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages: list[dict[str, Any]] = []
        self._pending_call_ids: set[str] = set()
        if messages:
            for message in messages:
                self._append_existing(message)

    @classmethod
    def from_user(cls, content: str) -> ToolConversation:
        conversation = cls()
        conversation.add_user(content)
        return conversation

    @property
    def messages(self) -> list[dict[str, Any]]:
        return deepcopy(self._messages)

    @property
    def pending_call_ids(self) -> frozenset[str]:
        return frozenset(self._pending_call_ids)

    def add_user(self, content: str) -> None:
        self._require_no_pending_calls()
        if not content.strip():
            raise ToolCallingProtocolError("User message must not be empty")
        self._messages.append({"role": "user", "content": content})

    def add_system(self, content: str) -> None:
        self._require_no_pending_calls()
        if self._messages:
            raise ToolCallingProtocolError("System message must be the first message")
        if not content.strip():
            raise ToolCallingProtocolError("System message must not be empty")
        self._messages.append({"role": "system", "content": content})

    def add_assistant(self, assistant_message: dict[str, Any]) -> None:
        self._require_no_pending_calls()
        normalized = _normalize_assistant_message(assistant_message)
        call_ids = [call["id"] for call in normalized.get("tool_calls", [])]
        if len(call_ids) != len(set(call_ids)):
            raise ToolCallingProtocolError(
                "Assistant message contains duplicate tool call IDs"
            )
        self._messages.append(normalized)
        self._pending_call_ids.update(call_ids)

    def add_tool_result(self, call_id: str, result: Any) -> None:
        if call_id not in self._pending_call_ids:
            raise ToolCallingProtocolError(
                f"Tool result references unknown or completed call ID '{call_id}'"
            )
        content = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, separators=(",", ":")
        )
        self._messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": content}
        )
        self._pending_call_ids.remove(call_id)

    def ensure_ready_for_model(self) -> None:
        self._require_no_pending_calls()
        if not self._messages:
            raise ToolCallingProtocolError("Conversation must contain at least one message")

    def _append_existing(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            raise ToolCallingProtocolError("Every conversation message must be an object")
        role = message.get("role")
        if role == "system":
            self.add_system(str(message.get("content") or ""))
        elif role == "user":
            self.add_user(str(message.get("content") or ""))
        elif role == "assistant":
            self.add_assistant(message)
        elif role == "tool":
            self.add_tool_result(
                str(message.get("tool_call_id") or ""), message.get("content", "")
            )
        else:
            raise ToolCallingProtocolError(f"Unsupported message role: {role!r}")

    def _require_no_pending_calls(self) -> None:
        if self._pending_call_ids:
            unresolved = ", ".join(sorted(self._pending_call_ids))
            raise ToolCallingProtocolError(
                f"Tool results are still missing for call IDs: {unresolved}"
            )


def _normalize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    if message.get("role") != "assistant":
        raise ToolCallingProtocolError("Assistant message must have role='assistant'")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ToolCallingProtocolError("Assistant content must be a string or null")
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise ToolCallingProtocolError("Assistant tool_calls must be a list")
    normalized_calls: list[dict[str, Any]] = []
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ToolCallingProtocolError("Each assistant tool call must be an object")
        call_id = raw_call.get("id")
        function = raw_call.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise ToolCallingProtocolError("Assistant tool call ID must not be empty")
        if raw_call.get("type") != "function" or not isinstance(function, dict):
            raise ToolCallingProtocolError("Only function tool calls are supported")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, str):
            raise ToolCallingProtocolError(
                "Assistant function calls require string name and arguments"
            )
        normalized_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    if not raw_calls and (not isinstance(content, str) or not content.strip()):
        raise ToolCallingProtocolError(
            "Assistant message must contain text or at least one tool call"
        )
    result: dict[str, Any] = {"role": "assistant", "content": content}
    if normalized_calls:
        result["tool_calls"] = normalized_calls
    return result
