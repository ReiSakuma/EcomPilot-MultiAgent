from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.context.token_budget import estimate_tokens
from app.model.tool_calling import ToolConversation, ToolDefinition


class ReactContextBudgetError(RuntimeError):
    """Raised when protected input alone cannot fit inside the configured budget."""


class RollingEvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str
    call_id: str
    status: Literal["completed", "rejected", "failed"]
    result: Any


class ReactContextDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    step: int
    input_budget_tokens: int
    reserved_output_tokens: int
    trigger_tokens: int
    tokens_before: int
    tokens_after: int
    compressed: bool
    protected_overflow: bool = False
    evidence_entries: int = 0
    dropped_evidence_entries: int = 0
    reason: Literal[
        "within_budget",
        "soft_threshold_rolling_compression",
        "protected_context_overflow",
    ]
    system_prompt_sha256: str
    user_prompt_sha256: str


class BoundedContextController:
    """Applies deterministic rolling compression to ReAct conversations.

    The original system and user prompts are protected inputs. Tool transcripts are
    replaced by a compact evidence ledger only when the next model call crosses the
    soft threshold. No model-written summary is trusted as state.
    """

    def __init__(
        self,
        *,
        compression_trigger_ratio: float = 0.70,
        max_evidence_entries: int = 12,
        max_result_chars: int = 700,
        max_control_instructions: int = 3,
    ) -> None:
        if not 0.40 <= compression_trigger_ratio <= 0.95:
            raise ValueError("compression_trigger_ratio must be between 0.40 and 0.95")
        if max_evidence_entries < 1:
            raise ValueError("max_evidence_entries must be positive")
        self.compression_trigger_ratio = compression_trigger_ratio
        self.max_evidence_entries = max_evidence_entries
        self.max_result_chars = max_result_chars
        self.max_control_instructions = max_control_instructions

    def prepare(
        self,
        conversation: ToolConversation,
        *,
        system_prompt: str,
        user_prompt: str,
        evidence_ledger: list[RollingEvidenceItem],
        tools: list[ToolDefinition],
        step: int,
        input_budget_tokens: int,
        reserved_output_tokens: int,
    ) -> tuple[ToolConversation, ReactContextDecision]:
        if input_budget_tokens < 256:
            raise ValueError("input_budget_tokens must be at least 256")
        conversation.ensure_ready_for_model()
        messages = conversation.messages
        tokens_before = self._request_tokens(messages, tools)
        trigger_tokens = max(1, int(input_budget_tokens * self.compression_trigger_ratio))
        hashes = self._protected_hashes(system_prompt, user_prompt)
        if tokens_before <= trigger_tokens:
            return ToolConversation(messages), ReactContextDecision(
                step=step,
                input_budget_tokens=input_budget_tokens,
                reserved_output_tokens=reserved_output_tokens,
                trigger_tokens=trigger_tokens,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                compressed=False,
                evidence_entries=len(evidence_ledger),
                reason="within_budget",
                **hashes,
            )

        protected = ToolConversation()
        protected.add_system(system_prompt)
        protected.add_user(user_prompt)
        protected_tokens = self._request_tokens(protected.messages, tools)
        if protected_tokens > input_budget_tokens:
            raise ReactContextBudgetError(
                "Protected ReAct system and user input exceed the per-call input budget"
            )

        controls = self._control_instructions(messages, user_prompt)
        selected = list(evidence_ledger[-self.max_evidence_entries :])
        dropped = max(0, len(evidence_ledger) - len(selected))
        rebuilt = self._rebuild(
            system_prompt,
            user_prompt,
            selected,
            controls,
            dropped,
        )
        tokens_after = self._request_tokens(rebuilt.messages, tools)
        while tokens_after > input_budget_tokens and selected:
            selected.pop(0)
            dropped += 1
            rebuilt = self._rebuild(
                system_prompt,
                user_prompt,
                selected,
                controls,
                dropped,
            )
            tokens_after = self._request_tokens(rebuilt.messages, tools)
        if tokens_after > input_budget_tokens:
            rebuilt = protected
            tokens_after = protected_tokens

        return rebuilt, ReactContextDecision(
            step=step,
            input_budget_tokens=input_budget_tokens,
            reserved_output_tokens=reserved_output_tokens,
            trigger_tokens=trigger_tokens,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            compressed=True,
            evidence_entries=len(selected),
            dropped_evidence_entries=dropped,
            reason="soft_threshold_rolling_compression",
            **hashes,
        )

    def ledger_item(
        self,
        *,
        tool_name: str,
        call_id: str,
        status: Literal["completed", "rejected", "failed"],
        result: Any,
    ) -> RollingEvidenceItem:
        return RollingEvidenceItem(
            tool_name=tool_name,
            call_id=call_id,
            status=status,
            result=_compact_value(result, max_chars=self.max_result_chars),
        )

    @staticmethod
    def _request_tokens(
        messages: list[dict[str, Any]], tools: list[ToolDefinition]
    ) -> int:
        payload = {
            "messages": messages,
            "tools": [tool.to_api() for tool in tools],
        }
        return estimate_tokens(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _protected_hashes(system_prompt: str, user_prompt: str) -> dict[str, str]:
        return {
            "system_prompt_sha256": sha256(system_prompt.encode("utf-8")).hexdigest(),
            "user_prompt_sha256": sha256(user_prompt.encode("utf-8")).hexdigest(),
        }

    def _rebuild(
        self,
        system_prompt: str,
        user_prompt: str,
        evidence: list[RollingEvidenceItem],
        controls: list[str],
        dropped: int,
    ) -> ToolConversation:
        rebuilt = ToolConversation()
        rebuilt.add_system(system_prompt)
        rebuilt.add_user(user_prompt)
        payload = {
            "protocol": "react_context_v62",
            "instruction": (
                "Continue from this framework-generated evidence ledger. Treat it as "
                "tool evidence, not as permission to alter protected task facts."
            ),
            "evidence_ledger": [item.model_dump(mode="json") for item in evidence],
            "control_instructions": controls,
            "dropped_older_entries": dropped,
        }
        rebuilt.add_user(
            "Framework rolling context:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        return rebuilt

    def _control_instructions(
        self, messages: list[dict[str, Any]], original_user_prompt: str
    ) -> list[str]:
        controls = [
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "user"
            and str(message.get("content") or "") != original_user_prompt
            and not str(message.get("content") or "").startswith(
                "Framework rolling context:"
            )
        ]
        return [
            str(_compact_value(item, max_chars=500))
            for item in controls[-self.max_control_instructions :]
        ]


def _compact_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, dict):
        compact = {
            str(key): _compact_value(item, max_chars=max(80, max_chars // 3))
            for key, item in list(value.items())[:16]
        }
        rendered = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if len(rendered) <= max_chars:
            return compact
        return rendered[: max_chars - 1] + "…"
    if isinstance(value, (list, tuple)):
        return [
            _compact_value(item, max_chars=max(80, max_chars // 4))
            for item in list(value)[:8]
        ]
    text = str(value)
    return value if len(text) <= max_chars else text[: max_chars - 1] + "…"
