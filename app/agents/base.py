from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.context.manager import ContextManager
from app.context.schemas import ContextPackage
from app.memory.long_term import LongTermMemory
from app.model.adapter import ModelAdapter
from app.model.policy import LlmPolicy, load_llm_policy
from app.model.structured import StructuredOutputError, parse_json_object
from app.model.telemetry import completed_model_record, failed_model_record
from app.orchestration.handoff import Handoff
from app.orchestration.state import TaskState
from app.tools.registry import ToolRegistry


ModelOutputT = TypeVar("ModelOutputT", bound=BaseModel)


class Agent(ABC):
    name: str

    def __init__(
        self,
        tools: ToolRegistry,
        context_manager: ContextManager | None = None,
        long_term_memory: LongTermMemory | None = None,
        model_adapter: ModelAdapter | None = None,
        llm_policy: LlmPolicy | None = None,
    ) -> None:
        self.tools = tools
        self.context_manager = context_manager or ContextManager()
        self.long_term_memory = long_term_memory or LongTermMemory()
        self.model_adapter = model_adapter or ModelAdapter()
        self.llm_policy = llm_policy or load_llm_policy()

    def build_context(self, state: TaskState, token_budget: int = 900) -> ContextPackage:
        scope = str(state.constraints.get("category", "global"))
        memory_snippets = self.long_term_memory.snippets(
            scope,
            tenant_id=state.principal.tenant_id,
            query=state.goal,
        )
        package = self.context_manager.build_for_agent(
            self.name, state, memory_snippets=memory_snippets, token_budget=token_budget
        )
        state.memory_refs[self.name] = package.memory_refs
        return package

    def llm_enabled(self) -> bool:
        return self.llm_policy.enabled_for(self.name)

    def generate_structured(
        self,
        state: TaskState,
        prompt: str,
        output_model: type[ModelOutputT],
        purpose: str,
    ) -> ModelOutputT | None:
        """Run one governed structured generation, with one optional JSON repair."""
        if not self.llm_enabled():
            return None

        schema = output_model.model_json_schema()
        try:
            response = self._call_model(state, prompt, schema, purpose)
            return self._validate_or_repair_structured(
                state,
                response.text,
                response.call_id,
                output_model,
                purpose,
            )
        except Exception as exc:
            return self._handle_model_failure(state, purpose, exc)

    def _validate_or_repair_structured(
        self,
        state: TaskState,
        text: str,
        call_id: str,
        output_model: type[ModelOutputT],
        purpose: str,
    ) -> ModelOutputT:
        """Validate structured output and allow at most one governed repair call."""
        schema = output_model.model_json_schema()
        try:
            output = output_model.model_validate(parse_json_object(text))
        except (StructuredOutputError, ValidationError) as exc:
            self._mark_structured_invalid(state, call_id, exc)
            if self.llm_policy.max_repair_attempts < 1:
                raise

            repaired = self._repair_model_output(
                state, text, str(exc), schema, purpose
            )
            try:
                output = output_model.model_validate(
                    parse_json_object(repaired.text)
                )
            except (StructuredOutputError, ValidationError) as repair_exc:
                self._mark_structured_invalid(state, repaired.call_id, repair_exc)
                raise
            self._mark_structured_valid(state, repaired.call_id)
            return output

        self._mark_structured_valid(state, call_id)
        return output

    def deterministic_mode(self, state: TaskState) -> str:
        if any(item.get("agent_name") == self.name for item in state.model_fallbacks):
            return "deterministic_fallback"
        return "deterministic"

    def _call_model(
        self,
        state: TaskState,
        prompt: str,
        schema: dict,
        purpose: str,
        *,
        max_output_tokens: int | None = None,
    ):
        self._enforce_call_budget(state)
        try:
            response = (
                self.model_adapter.complete(prompt, json_schema=schema)
                if max_output_tokens is None
                else self.model_adapter.complete(
                    prompt,
                    json_schema=schema,
                    max_output_tokens=max_output_tokens,
                )
            )
        except Exception as exc:
            state.model_records.append(failed_model_record(
                self.model_adapter, exc, agent_name=self.name, purpose=purpose
            ))
            raise
        state.model_records.append(completed_model_record(
            response,
            agent_name=self.name,
            purpose=purpose,
            structured_validation="pending",
        ))
        return response

    def _repair_model_output(
        self,
        state: TaskState,
        bad_text: str,
        error: str,
        schema: dict,
        purpose: str,
    ):
        self._enforce_call_budget(state)
        try:
            response = self.model_adapter.repair_json(bad_text, error, schema)
        except Exception as exc:
            state.model_records.append(failed_model_record(
                self.model_adapter,
                exc,
                agent_name=self.name,
                purpose=f"{purpose}_repair",
            ))
            raise
        state.model_records.append(completed_model_record(
            response,
            agent_name=self.name,
            purpose=f"{purpose}_repair",
            structured_validation="pending",
        ))
        return response

    def _enforce_call_budget(self, state: TaskState) -> None:
        call_count = sum(
            1 for record in state.model_records if record.get("agent_name") == self.name
        )
        if call_count >= self.llm_policy.max_calls_per_agent:
            raise RuntimeError(
                f"LLM call budget exhausted for {self.name}: "
                f"{call_count}/{self.llm_policy.max_calls_per_agent}"
            )

    @staticmethod
    def _mark_structured_valid(state: TaskState, call_id: str) -> None:
        for record in reversed(state.model_records):
            if record.get("call_id") == call_id:
                record["structured_validation"] = "passed"
                return

    @staticmethod
    def _mark_structured_invalid(state: TaskState, call_id: str, exc: Exception) -> None:
        for record in reversed(state.model_records):
            if record.get("call_id") == call_id:
                record["structured_validation"] = "failed"
                record["validation_error"] = str(exc)
                return

    def _handle_model_failure(
        self, state: TaskState, purpose: str, exc: Exception
    ) -> None:
        if self.llm_policy.fallback_mode == "fail_closed":
            raise exc
        state.model_fallbacks.append(
            {
                "agent_name": self.name,
                "purpose": purpose,
                "provider": self.model_adapter.provider,
                "model": self.model_adapter.model,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "fallback": "deterministic",
            }
        )
        return None

    @abstractmethod
    def run(self, state: TaskState) -> Handoff:
        raise NotImplementedError
