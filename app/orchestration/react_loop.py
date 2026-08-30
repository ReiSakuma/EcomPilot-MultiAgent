from __future__ import annotations

import json
from collections.abc import Callable
from inspect import Parameter, signature
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.context.react_budget import (
    BoundedContextController,
    ReactContextBudgetError,
    ReactContextDecision,
    RollingEvidenceItem,
)
from app.model.adapter import ModelAdapter, ModelResponse
from app.model.tool_calling import ModelToolCall, ToolConversation
from app.safety.policy_gateway import PolicyContext
from app.tools.governed_executor import GovernedToolExecutor


class ReactLoopError(RuntimeError):
    safe_to_retry = False


class ReactLoopLimitError(ReactLoopError):
    pass


class ReactLoopTimeoutError(ReactLoopError):
    pass


class ReactRepeatedActionError(ReactLoopError):
    pass


class ReactToolConstraintError(ReactLoopError):
    pass


class ReactLoopConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(default=4, ge=1, le=20)
    max_tool_calls: int = Field(default=6, ge=1, le=100)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_identical_actions: int = Field(default=1, ge=1, le=3)
    input_token_budget: int = Field(default=12000, ge=256, le=100000)
    max_output_tokens: int = Field(default=1600, ge=128, le=16000)
    compression_trigger_ratio: float = Field(default=0.70, ge=0.40, le=0.95)


class ReactStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int
    model_call_id: str
    action: Literal["tool_calls", "tool_rejected", "incomplete_final", "final"]
    tool_call_ids: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    missing_required_tools: tuple[str, ...] = ()
    forced_finalization: bool = False


class ReactLoopResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    final_text: str
    final_call_id: str
    stop_reason: Literal["model_final"] = "model_final"
    steps: tuple[ReactStep, ...]
    tool_results: dict[str, Any]
    tool_results_by_call_id: dict[str, Any]
    tool_call_ids_by_name: dict[str, tuple[str, ...]]
    tool_call_count: int
    elapsed_ms: float
    context_decisions: tuple[ReactContextDecision, ...] = ()
    compression_count: int = 0

    def context_budget_summary(self) -> dict[str, Any]:
        decisions = list(self.context_decisions)
        return {
            "protocol_version": "1.0",
            "model_calls": len(decisions),
            "compression_count": self.compression_count,
            "max_tokens_before": max(
                (item.tokens_before for item in decisions), default=0
            ),
            "max_tokens_after": max(
                (item.tokens_after for item in decisions), default=0
            ),
            "input_budget_tokens": (
                decisions[-1].input_budget_tokens if decisions else 0
            ),
            "reserved_output_tokens": (
                decisions[-1].reserved_output_tokens if decisions else 0
            ),
            "decisions": [item.model_dump(mode="json") for item in decisions],
        }


ModelRecordCallback = Callable[[ModelResponse, int], None]
ModelErrorCallback = Callable[[Exception, int], None]
ToolConstraintValidator = Callable[[ModelToolCall, dict[str, Any]], None]
ToolBatchConstraintValidator = Callable[
    [tuple[ModelToolCall, ...], dict[str, Any]], None
]
ToolErrorFeedback = Callable[[Exception], dict[str, Any] | None]
ToolResultProjector = Callable[[str, Any], Any]


class BoundedReactLoop:
    """Runs model -> governed tools -> model with explicit termination limits."""

    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_executor: GovernedToolExecutor,
        config: ReactLoopConfig | None = None,
        clock: Callable[[], float] | None = None,
        context_controller: BoundedContextController | None = None,
    ) -> None:
        self.model_adapter = model_adapter
        self.tool_executor = tool_executor
        self.config = config or ReactLoopConfig()
        self._clock = clock or perf_counter
        self.context_controller = context_controller or BoundedContextController(
            compression_trigger_ratio=self.config.compression_trigger_ratio
        )
        self._observer: Callable[[dict[str, Any]], None] | None = None

    def set_observer(self, observer: Callable[[dict[str, Any]], None] | None) -> None:
        self._observer = observer

    def run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        policy_context: PolicyContext,
        required_tools: set[str] | None = None,
        tool_descriptions: dict[str, str] | None = None,
        validate_tool_batch: ToolBatchConstraintValidator | None = None,
        validate_tool_call: ToolConstraintValidator | None = None,
        project_tool_result: ToolResultProjector | None = None,
        tool_error_feedback: ToolErrorFeedback | None = None,
        max_tool_error_recoveries: int = 0,
        force_final_after_tool_calls: int | None = None,
        on_model_response: ModelRecordCallback | None = None,
        on_model_error: ModelErrorCallback | None = None,
        evidence_ledger: list[RollingEvidenceItem] | None = None,
        input_token_budget: int | None = None,
        max_output_tokens: int | None = None,
    ) -> ReactLoopResult:
        required_tools = required_tools or set()
        definitions = self.tool_executor.definitions_for(
            policy_context, descriptions=tool_descriptions
        )
        available_names = {definition.name for definition in definitions}
        unavailable = required_tools - available_names
        if unavailable:
            raise ReactToolConstraintError(
                f"Required ReAct tools are unavailable: {sorted(unavailable)}"
            )

        conversation = ToolConversation()
        conversation.add_system(system_prompt)
        conversation.add_user(user_prompt)
        started = self._clock()
        steps: list[ReactStep] = []
        tool_results: dict[str, Any] = {}
        tool_results_by_call_id: dict[str, Any] = {}
        tool_call_ids_by_name: dict[str, list[str]] = {}
        used_tools: set[str] = set()
        action_counts: dict[str, int] = {}
        tool_call_count = 0
        tool_error_recoveries = 0
        rolling_evidence = list(evidence_ledger or [])
        context_decisions: list[ReactContextDecision] = []
        call_input_budget = input_token_budget or self.config.input_token_budget
        call_output_budget = max_output_tokens or self.config.max_output_tokens
        if force_final_after_tool_calls is not None and force_final_after_tool_calls < 1:
            raise ValueError("force_final_after_tool_calls must be at least 1")
        if (
            force_final_after_tool_calls is not None
            and force_final_after_tool_calls >= self.config.max_steps
        ):
            raise ValueError(
                "force_final_after_tool_calls must reserve one configured step "
                "for the final answer"
            )
        total_step_limit = self.config.max_steps + max(
            0, max_tool_error_recoveries
        )
        finalization_instruction_added = False

        def recover_tool_error(
            error: Exception, response: ModelResponse, step_number: int
        ) -> bool:
            nonlocal tool_error_recoveries
            feedback = (
                tool_error_feedback(error)
                if tool_error_feedback is not None
                else None
            )
            if (
                feedback is None
                or tool_error_recoveries >= max_tool_error_recoveries
            ):
                return False
            if response.assistant_message is None:
                raise ReactLoopError(
                    "Rejected tool-call response did not include assistant_message"
                ) from error
            conversation.add_assistant(response.assistant_message)
            for call in response.tool_calls:
                rejected_result = feedback | {
                    "tool_name": call.name,
                    "executed": False,
                }
                conversation.add_tool_result(
                    call.call_id,
                    rejected_result,
                )
                rolling_evidence.append(
                    self.context_controller.ledger_item(
                        tool_name=call.name,
                        call_id=call.call_id,
                        status="rejected",
                        result=rejected_result,
                    )
                )
            tool_error_recoveries += 1
            react_step = ReactStep(
                step=step_number,
                model_call_id=response.call_id,
                action="tool_rejected",
                tool_call_ids=tuple(call.call_id for call in response.tool_calls),
                tool_names=tuple(call.name for call in response.tool_calls),
            )
            steps.append(react_step)
            self._notify(
                policy_context,
                step_number,
                "tool_rejected",
                response.call_id,
                tool_names=list(react_step.tool_names),
                error=error,
            )
            return True

        for step_number in range(1, total_step_limit + 1):
            self._check_deadline(started, policy_context, step_number)
            force_final = bool(
                force_final_after_tool_calls is not None
                and tool_call_count >= force_final_after_tool_calls
            )
            if force_final and not finalization_instruction_added:
                conversation.add_user(
                    "The tool exploration budget is exhausted. Do not call any more "
                    "tools. Return the required final JSON now using the evidence "
                    "already collected; explicitly state uncertainty when evidence "
                    "is insufficient."
                )
                finalization_instruction_added = True
            try:
                conversation, context_decision = self.context_controller.prepare(
                    conversation,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    evidence_ledger=rolling_evidence,
                    tools=definitions,
                    step=step_number,
                    input_budget_tokens=call_input_budget,
                    reserved_output_tokens=call_output_budget,
                )
            except ReactContextBudgetError as exc:
                error = ReactLoopLimitError(str(exc))
                self._notify(
                    policy_context,
                    step_number,
                    "context_budget_failed",
                    error=error,
                )
                raise error from exc
            context_decisions.append(context_decision)
            if context_decision.compressed:
                self._notify_context(policy_context, context_decision)
            try:
                response = self._complete_with_budget(
                    conversation.messages,
                    definitions,
                    tool_choice="none" if force_final else "auto",
                    max_output_tokens=call_output_budget,
                )
            except Exception as exc:
                if on_model_error is not None:
                    on_model_error(exc, step_number)
                self._notify(
                    policy_context,
                    step_number,
                    "failed",
                    model_call_id=getattr(exc, "model_call_id", None),
                    error=exc,
                )
                raise
            if on_model_response is not None:
                on_model_response(response, step_number)
            self._check_deadline(
                started, policy_context, step_number, response.call_id
            )

            if response.tool_calls:
                if force_final:
                    error = ReactLoopLimitError(
                        "Model attempted another tool call during forced finalization"
                    )
                    self._notify(
                        policy_context,
                        step_number,
                        "failed",
                        response.call_id,
                        tool_names=[call.name for call in response.tool_calls],
                        error=error,
                    )
                    raise error
                if tool_call_count + len(response.tool_calls) > self.config.max_tool_calls:
                    error = ReactLoopLimitError(
                        f"ReAct tool-call budget exceeded: "
                        f"{tool_call_count + len(response.tool_calls)}/"
                        f"{self.config.max_tool_calls}"
                    )
                    self._notify(
                        policy_context,
                        step_number,
                        "failed",
                        response.call_id,
                        tool_names=[call.name for call in response.tool_calls],
                        error=error,
                    )
                    raise error
                batch_signatures: list[str] = []
                try:
                    if validate_tool_batch is not None:
                        validate_tool_batch(
                            tuple(response.tool_calls), dict(tool_results)
                        )
                    for call in response.tool_calls:
                        signature = _action_signature(call)
                        attempted_count = action_counts.get(
                            signature, 0
                        ) + batch_signatures.count(signature) + 1
                        if attempted_count > self.config.max_identical_actions:
                            raise ReactRepeatedActionError(
                                f"Repeated ReAct action blocked for tool '{call.name}'"
                            )
                        if validate_tool_call is not None:
                            validate_tool_call(call, dict(tool_results))
                        batch_signatures.append(signature)
                except Exception as error:
                    if recover_tool_error(error, response, step_number):
                        continue
                    self._notify(
                        policy_context,
                        step_number,
                        "failed",
                        response.call_id,
                        tool_names=[
                            call.name for call in response.tool_calls
                        ],
                        error=error,
                    )
                    raise
                for signature in batch_signatures:
                    action_counts[signature] = action_counts.get(signature, 0) + 1

                try:
                    outcomes = self.tool_executor.execute_many(
                        response.tool_calls, policy_context
                    )
                except Exception as error:
                    if recover_tool_error(error, response, step_number):
                        continue
                    self._notify(
                        policy_context,
                        step_number,
                        "failed",
                        response.call_id,
                        tool_names=[call.name for call in response.tool_calls],
                        error=error,
                    )
                    raise
                self._check_deadline(
                    started, policy_context, step_number, response.call_id
                )
                if response.assistant_message is None:
                    raise ReactLoopError("Tool-call response did not include assistant_message")
                conversation.add_assistant(response.assistant_message)
                for outcome in outcomes:
                    model_result = (
                        project_tool_result(outcome.tool_name, outcome.result)
                        if project_tool_result is not None
                        else outcome.result
                    )
                    conversation.add_tool_result(outcome.call_id, model_result)
                    rolling_evidence.append(
                        self.context_controller.ledger_item(
                            tool_name=outcome.tool_name,
                            call_id=outcome.call_id,
                            status="completed",
                            result=model_result,
                        )
                    )
                    tool_results[outcome.tool_name] = outcome.result
                    tool_results_by_call_id[outcome.call_id] = outcome.result
                    tool_call_ids_by_name.setdefault(outcome.tool_name, []).append(
                        outcome.call_id
                    )
                    used_tools.add(outcome.tool_name)
                tool_call_count += len(outcomes)
                react_step = ReactStep(
                    step=step_number,
                    model_call_id=response.call_id,
                    action="tool_calls",
                    tool_call_ids=tuple(call.call_id for call in response.tool_calls),
                    tool_names=tuple(call.name for call in response.tool_calls),
                )
                steps.append(react_step)
                self._notify(
                    policy_context,
                    step_number,
                    "tool_calls",
                    response.call_id,
                    tool_names=list(react_step.tool_names),
                )
                continue

            missing = required_tools - used_tools
            if missing:
                if response.assistant_message is None:
                    raise ReactLoopError("Final response did not include assistant_message")
                conversation.add_assistant(response.assistant_message)
                conversation.add_user(
                    "Required tool evidence is still missing. Call these tools before "
                    f"the final answer: {', '.join(sorted(missing))}."
                )
                react_step = ReactStep(
                    step=step_number,
                    model_call_id=response.call_id,
                    action="incomplete_final",
                    missing_required_tools=tuple(sorted(missing)),
                )
                steps.append(react_step)
                self._notify(
                    policy_context,
                    step_number,
                    "incomplete_final",
                    response.call_id,
                    missing_tools=sorted(missing),
                )
                continue

            react_step = ReactStep(
                step=step_number,
                model_call_id=response.call_id,
                action="final",
                forced_finalization=force_final,
            )
            steps.append(react_step)
            elapsed_ms = round((self._clock() - started) * 1000, 2)
            self._notify(
                policy_context,
                step_number,
                "completed",
                response.call_id,
            )
            return ReactLoopResult(
                final_text=response.text,
                final_call_id=response.call_id,
                steps=tuple(steps),
                tool_results=tool_results,
                tool_results_by_call_id=tool_results_by_call_id,
                tool_call_ids_by_name={
                    name: tuple(call_ids)
                    for name, call_ids in tool_call_ids_by_name.items()
                },
                tool_call_count=tool_call_count,
                elapsed_ms=elapsed_ms,
                context_decisions=tuple(context_decisions),
                compression_count=sum(
                    1 for decision in context_decisions if decision.compressed
                ),
            )

        error = ReactLoopLimitError(
            f"ReAct step limit exhausted: {total_step_limit}"
        )
        self._notify(
            policy_context,
            total_step_limit,
            "failed",
            error=error,
        )
        raise error

    def _complete_with_budget(
        self,
        messages: list[dict[str, Any]],
        definitions,
        *,
        tool_choice: str,
        max_output_tokens: int,
    ) -> ModelResponse:
        method = self.model_adapter.complete_with_tools
        parameters = signature(method).parameters.values()
        supports_budget = "max_output_tokens" in signature(method).parameters or any(
            parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters
        )
        if supports_budget:
            return method(
                messages,
                definitions,
                tool_choice=tool_choice,
                max_output_tokens=max_output_tokens,
            )
        return method(messages, definitions, tool_choice=tool_choice)

    def _notify_context(
        self, context: PolicyContext, decision: ReactContextDecision
    ) -> None:
        if self._observer is None:
            return
        try:
            self._observer(
                {
                    "event_type": "react_context_budget",
                    "component_type": "orchestrator",
                    "component_name": "bounded_context_controller",
                    "agent_name": context.principal.agent_name,
                    "step": f"react.{decision.step}.context",
                    "status": "compressed",
                    "details": decision.model_dump(mode="json"),
                    "error": None,
                }
            )
        except Exception:
            return

    def _assert_within_deadline(self, started: float) -> None:
        elapsed = self._clock() - started
        if elapsed > self.config.timeout_seconds:
            raise ReactLoopTimeoutError(
                f"ReAct loop exceeded {self.config.timeout_seconds:g} seconds"
            )

    def _check_deadline(
        self,
        started: float,
        context: PolicyContext,
        step: int,
        model_call_id: str | None = None,
    ) -> None:
        try:
            self._assert_within_deadline(started)
        except ReactLoopTimeoutError as error:
            self._notify(
                context,
                step,
                "failed",
                model_call_id,
                error=error,
            )
            raise

    def _notify(
        self,
        context: PolicyContext,
        step: int,
        status: str,
        model_call_id: str | None = None,
        *,
        tool_names: list[str] | None = None,
        missing_tools: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        if self._observer is None:
            return
        try:
            self._observer(
                {
                    "event_type": "react_step",
                    "component_type": "orchestrator",
                    "component_name": "bounded_react_loop",
                    "agent_name": context.principal.agent_name,
                    "step": f"react.{step}",
                    "status": status,
                    "details": {
                        "task_id": context.principal.task_id,
                        "tenant_id": context.principal.tenant_id,
                        "model_call_id": model_call_id,
                        "tool_names": tool_names or [],
                        "missing_required_tools": missing_tools or [],
                    },
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                    if error
                    else None,
                }
            )
        except Exception:
            return


def _action_signature(call: ModelToolCall) -> str:
    return json.dumps(
        {"name": call.name, "arguments": call.arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
