from __future__ import annotations

import json
import http.client
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from email.message import Message
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
)
from app.model.pricing import estimate_cost_usd
from app.distributed.bulkhead import GLOBAL_BULKHEADS
from app.model.tool_calling import (
    ModelToolCall,
    ToolCallingProtocolError,
    ToolConversation,
    ToolDefinition,
    UnknownToolCallError,
)


class ModelResponse(BaseModel):
    call_id: str
    provider: str
    model: str
    text: str = ""
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    assistant_message: dict[str, Any] | None = None
    response_id: str | None = None
    response_status: str = "completed"
    finish_reason: str | None = None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_source: str
    prompt_tokens_estimate: int
    completion_tokens_estimate: int
    request_input_tokens_estimate: int = 0
    requested_max_output_tokens: int = 0
    cost_usd_estimate: float = 0.0
    request_attempts: int = 1
    duration_ms: float = 0.0
    structured_output_mode: str = "none"
    repaired: bool = False
    error: str | None = None


class ModelProviderError(RuntimeError):
    retryable = False


class ModelConfigurationError(ModelProviderError):
    pass


class ModelAuthenticationError(ModelProviderError):
    pass


class ModelRateLimitError(ModelProviderError):
    retryable = True
    safe_to_retry = True


class ModelTransientError(ModelProviderError):
    retryable = True
    safe_to_retry = True


class ModelResponseError(ModelProviderError):
    pass


class ModelIncompleteError(ModelResponseError):
    # Repeating the same transport response is not useful, but regenerating the
    # read-only agent stage from its checkpoint is safe.
    safe_to_retry = True


class ModelAdapter:
    """Responses API boundary with structured output, retries, usage, and tracing."""

    def __init__(
        self,
        provider: str = "deterministic",
        model: str = "local-rule-v6",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key if api_key is not None else LLM_API_KEY
        self.base_url = (base_url or LLM_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds or LLM_TIMEOUT_SECONDS
        self.max_retries = LLM_MAX_RETRIES if max_retries is None else max_retries
        self.max_output_tokens = max_output_tokens or LLM_MAX_OUTPUT_TOKENS
        self._observer: Callable[[dict[str, Any]], None] | None = None
        self._agent_context: ContextVar[str] = ContextVar("model_agent_name", default="unknown")

    def set_observer(self, observer: Callable[[dict[str, Any]], None] | None) -> None:
        self._observer = observer

    @contextmanager
    def agent_scope(self, agent_name: str):
        token = self._agent_context.set(agent_name)
        try:
            yield
        finally:
            self._agent_context.reset(token)

    def complete(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        *,
        max_output_tokens: int | None = None,
    ) -> ModelResponse:
        with GLOBAL_BULKHEADS.acquire("model", blocking=True):
            return self._complete_isolated(
                prompt,
                json_schema,
                max_output_tokens=max_output_tokens,
            )

    def _complete_isolated(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        *,
        max_output_tokens: int | None = None,
    ) -> ModelResponse:
        call_id = f"model_{uuid4().hex[:12]}"
        started = perf_counter()
        effective_max_output_tokens = max_output_tokens or self.max_output_tokens
        request_input_tokens_estimate = max(1, len(prompt) // 4)
        finish_reason = None
        try:
            if self.provider == "deterministic":
                text = self._deterministic_complete(prompt)
                input_tokens = max(1, len(prompt) // 4)
                output_tokens = max(1, len(text) // 4)
                response_id = None
                response_status = "completed"
                usage_source = "estimated"
                attempts = 1
                finish_reason = None
                structured_output_mode = "local_fixture" if json_schema else "none"
            elif self.provider in {"openai", "openai-compatible"}:
                body, attempts = self._responses_api_complete(
                    prompt,
                    json_schema=json_schema,
                    max_output_tokens=effective_max_output_tokens,
                )
                response_status = str(body.get("status") or "completed")
                self._validate_response_status(body, response_status)
                text = self._extract_output_text(body)
                input_tokens, output_tokens, usage_source = self._extract_usage(
                    body, prompt, text
                )
                response_id = str(body["id"]) if body.get("id") else None
                finish_reason = None
                structured_output_mode = "strict_json_schema" if json_schema else "none"
            elif self.provider == "deepseek":
                body, attempts = self._chat_completions_api_complete(
                    prompt,
                    json_schema=json_schema,
                    max_output_tokens=effective_max_output_tokens,
                )
                finish_reason = self._extract_chat_finish_reason(body)
                self._validate_chat_finish_reason(finish_reason)
                text = self._extract_chat_output_text(body)
                input_tokens, output_tokens, usage_source = self._extract_usage(
                    body, prompt, text
                )
                response_id = str(body["id"]) if body.get("id") else None
                response_status = "completed"
                structured_output_mode = "json_object_local_schema" if json_schema else "none"
            else:
                raise ModelConfigurationError(
                    f"Unsupported model provider: {self.provider}"
                )
            response = ModelResponse(
                call_id=call_id,
                provider=self.provider,
                model=self.model,
                text=text,
                response_id=response_id,
                response_status=response_status,
                finish_reason=finish_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                usage_source=usage_source,
                prompt_tokens_estimate=input_tokens,
                completion_tokens_estimate=output_tokens,
                request_input_tokens_estimate=request_input_tokens_estimate,
                requested_max_output_tokens=effective_max_output_tokens,
                cost_usd_estimate=estimate_cost_usd(
                    self.model, input_tokens, output_tokens
                ),
                request_attempts=attempts,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                structured_output_mode=structured_output_mode,
            )
        except Exception as exc:
            setattr(exc, "model_call_id", call_id)
            setattr(exc, "model_input_token_estimate", request_input_tokens_estimate)
            setattr(exc, "model_requested_max_output_tokens", effective_max_output_tokens)
            setattr(exc, "model_finish_reason", finish_reason)
            self._notify(
                call_id,
                prompt,
                json_schema,
                round((perf_counter() - started) * 1000, 2),
                response=None,
                error=exc,
            )
            raise
        self._notify(
            call_id,
            prompt,
            json_schema,
            response.duration_ms,
            response=response,
            error=None,
        )
        return response

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_choice: str = "auto",
        *,
        max_output_tokens: int | None = None,
    ) -> ModelResponse:
        with GLOBAL_BULKHEADS.acquire("model", blocking=True):
            return self._complete_with_tools_isolated(
                messages,
                tools,
                tool_choice,
                max_output_tokens=max_output_tokens,
            )

    def _complete_with_tools_isolated(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_choice: str = "auto",
        *,
        max_output_tokens: int | None = None,
    ) -> ModelResponse:
        """Ask DeepSeek to answer or request validated function tool calls.

        This method implements only the provider protocol boundary. The caller owns
        policy checks and execution of any returned tool calls.
        """
        call_id = f"model_{uuid4().hex[:12]}"
        started = perf_counter()
        prompt = json.dumps(messages, ensure_ascii=False)
        request_input_tokens_estimate = max(1, len(prompt) // 4)
        requested_max_output_tokens = max_output_tokens or self.max_output_tokens
        finish_reason = None
        trace_prompt = json.dumps(
            {
                "message_count": len(messages),
                "roles": [message.get("role") for message in messages],
                "tool_calling": True,
            },
            ensure_ascii=False,
        )
        try:
            if self.provider != "deepseek":
                raise ModelConfigurationError(
                    "V17 tool calling currently supports provider='deepseek' only"
                )
            if tool_choice not in {"auto", "required", "none"}:
                raise ToolCallingProtocolError(
                    "tool_choice must be 'auto', 'required', or 'none'"
                )
            if not tools:
                raise ToolCallingProtocolError("At least one tool definition is required")
            tools_by_name = {tool.name: tool for tool in tools}
            if len(tools_by_name) != len(tools):
                raise ToolCallingProtocolError("Tool names must be unique")

            conversation = ToolConversation(messages)
            conversation.ensure_ready_for_model()
            body, attempts = self._chat_completions_tools_api_complete(
                conversation.messages,
                tools,
                tool_choice,
                max_output_tokens=requested_max_output_tokens,
            )
            finish_reason = self._extract_chat_finish_reason(body)
            self._validate_chat_finish_reason(finish_reason, allow_tool_calls=True)
            message = self._extract_chat_message(body)
            tool_calls = self._extract_validated_tool_calls(message, tools_by_name)
            content = message.get("content")
            text = content if isinstance(content, str) else ""
            if tool_calls and finish_reason != "tool_calls":
                raise ModelResponseError(
                    "DeepSeek returned tool calls without finish_reason=tool_calls"
                )
            if finish_reason == "tool_calls" and not tool_calls:
                raise ModelResponseError(
                    "DeepSeek returned finish_reason=tool_calls without tool calls"
                )
            if not tool_calls and not text.strip():
                raise ModelResponseError(
                    "DeepSeek response contained neither text nor tool calls"
                )

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": content if isinstance(content, str) else None,
            }
            if tool_calls:
                assistant_message["tool_calls"] = [
                    tool_call.to_api() for tool_call in tool_calls
                ]
            input_tokens, output_tokens, usage_source = self._extract_usage(
                body, prompt, text or json.dumps(assistant_message, ensure_ascii=False)
            )
            response = ModelResponse(
                call_id=call_id,
                provider=self.provider,
                model=self.model,
                text=text,
                tool_calls=tool_calls,
                assistant_message=assistant_message,
                response_id=str(body["id"]) if body.get("id") else None,
                response_status="completed",
                finish_reason=finish_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                usage_source=usage_source,
                prompt_tokens_estimate=input_tokens,
                completion_tokens_estimate=output_tokens,
                request_input_tokens_estimate=request_input_tokens_estimate,
                requested_max_output_tokens=requested_max_output_tokens,
                cost_usd_estimate=estimate_cost_usd(
                    self.model, input_tokens, output_tokens
                ),
                request_attempts=attempts,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                structured_output_mode="tool_calling",
            )
        except Exception as exc:
            setattr(exc, "model_call_id", call_id)
            setattr(exc, "model_input_token_estimate", request_input_tokens_estimate)
            setattr(exc, "model_requested_max_output_tokens", requested_max_output_tokens)
            setattr(exc, "model_finish_reason", finish_reason)
            self._notify(
                call_id,
                trace_prompt,
                None,
                round((perf_counter() - started) * 1000, 2),
                response=None,
                error=exc,
            )
            raise
        self._notify(
            call_id,
            trace_prompt,
            None,
            response.duration_ms,
            response=response,
            error=None,
        )
        return response

    def repair_json(
        self, bad_text: str, error: str, json_schema: dict[str, Any]
    ) -> ModelResponse:
        repair_prompt = (
            "请把下面的模型输出修复为严格 JSON object。"
            "不要解释，不要输出 Markdown，只输出 JSON。\n\n"
            f"解析错误: {error}\n\n原始输出:\n{bad_text}\n\nJSON Schema:\n"
            f"{json.dumps(json_schema, ensure_ascii=False)}"
        )
        response = self.complete(repair_prompt, json_schema=json_schema)
        return response.model_copy(update={"repaired": True})

    def _deterministic_complete(self, prompt: str) -> str:
        if "Listing Agent" in prompt:
            return json.dumps(
                {
                    "title": "低延迟长续航无线耳机 主动降噪蓝牙耳机 适合大学生",
                    "keywords": ["蓝牙耳机", "无线耳机", "降噪耳机", "低延迟耳机", "学生党耳机", "宿舍通话"],
                    "bullets": [
                        "低延迟模式适合网课、游戏和短视频场景",
                        "轻量佩戴设计，降低长时间使用负担",
                        "长续航充电盒，覆盖通勤、宿舍和自习场景",
                        "突出稳定连接与清晰通话，回应竞品常见痛点",
                    ],
                    "compliance_notes": ["未承诺医疗功效", "未使用绝对化营销词", "LLM 输出已结构化校验"],
                },
                ensure_ascii=False,
            )
        if "Strategy Agent" in prompt or "ecommerce launch strategy" in prompt:
            return json.dumps(
                {
                    "launch_plan": "首月主推学生场景，前两周用限量券拉动冷启动，第三周收窄优惠观察转化。",
                    "rationale": "优惠策略必须以确定性毛利和库存工具结果为准。",
                    "discount_amount_yuan": 10,
                },
                ensure_ascii=False,
            )
        if "Review Agent" in prompt or "电商语义审核器" in prompt:
            return json.dumps(
                {"issues": []},
                ensure_ascii=False,
            )
        return json.dumps({"message": "V14 deterministic model response"}, ensure_ascii=False)

    def _responses_api_complete(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        *,
        max_output_tokens: int,
    ) -> tuple[dict[str, Any], int]:
        if not self.api_key:
            raise ModelConfigurationError(
                "Missing API key. Set ECOMPILOT_LLM_API_KEY or OPENAI_API_KEY."
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if json_schema is not None:
            schema_name = f"ecompilot_{self._agent_context.get()}_output"[:64]
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                }
            }
        return self._post_json("/responses", payload)

    def _chat_completions_api_complete(
        self,
        prompt: str,
        json_schema: dict[str, Any] | None = None,
        *,
        max_output_tokens: int,
    ) -> tuple[dict[str, Any], int]:
        if not self.api_key:
            raise ModelConfigurationError(
                "Missing API key. Set ECOMPILOT_LLM_API_KEY or DEEPSEEK_API_KEY."
            )
        user_prompt = prompt
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        if json_schema is not None:
            user_prompt = (
                f"{prompt}\n\nReturn one JSON object that conforms to this JSON Schema. "
                "Do not output Markdown or explanatory text.\n"
                f"JSON Schema:\n{json.dumps(json_schema, ensure_ascii=False)}"
            )
            payload["messages"][0]["content"] = user_prompt
            payload["response_format"] = {"type": "json_object"}
        if self.provider == "deepseek":
            # DeepSeek reasoning tokens share the completion budget. Structured
            # business calls need the JSON result, not an unbounded hidden trace.
            payload["thinking"] = {"type": "disabled"}
        return self._post_json("/chat/completions", payload)

    def _chat_completions_tools_api_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        tool_choice: str,
        *,
        max_output_tokens: int,
    ) -> tuple[dict[str, Any], int]:
        if not self.api_key:
            raise ModelConfigurationError(
                "Missing API key. Set ECOMPILOT_LLM_API_KEY or DEEPSEEK_API_KEY."
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "stream": False,
            "tools": [tool.to_api() for tool in tools],
            "tool_choice": tool_choice,
            "thinking": {"type": "disabled"},
        }
        return self._post_json("/chat/completions", payload)

    def _post_json(
        self, endpoint: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        if not self.api_key:
            raise ModelConfigurationError(
                "Missing API key. Set ECOMPILOT_LLM_API_KEY, OPENAI_API_KEY, "
                "or DEEPSEEK_API_KEY."
            )
        data = json.dumps(payload).encode("utf-8")
        last_error: ModelProviderError | None = None
        for attempt in range(1, self.max_retries + 2):
            request = urllib.request.Request(
                f"{self.base_url}{endpoint}",
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "EcomPilot-MultiAgent/v15-interview",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict):
                    raise ModelResponseError("LLM API returned a non-object body")
                return body, attempt
            except urllib.error.HTTPError as exc:
                mapped = self._map_http_error(exc)
                last_error = mapped
                setattr(mapped, "model_request_attempts", attempt)
                if not mapped.retryable or attempt > self.max_retries:
                    raise mapped from exc
                self._sleep_before_retry(attempt, exc.headers)
            except (
                urllib.error.URLError,
                TimeoutError,
                ssl.SSLError,
                http.client.RemoteDisconnected,
                http.client.IncompleteRead,
                ConnectionError,
                BrokenPipeError,
            ) as exc:
                last_error = ModelTransientError(f"LLM transport failed: {exc}")
                setattr(last_error, "model_request_attempts", attempt)
                if attempt > self.max_retries:
                    raise last_error from exc
                self._sleep_before_retry(attempt)
            except json.JSONDecodeError as exc:
                last_error = ModelTransientError(
                    f"LLM response was not valid JSON: {exc}"
                )
                setattr(last_error, "model_request_attempts", attempt)
                if attempt > self.max_retries:
                    raise last_error from exc
                self._sleep_before_retry(attempt)
        raise last_error or ModelProviderError("LLM request failed")

    @staticmethod
    def _map_http_error(exc: urllib.error.HTTPError) -> ModelProviderError:
        try:
            body = exc.read().decode("utf-8")[:1000]
        except Exception:
            body = ""
        detail = f"HTTP {exc.code}" + (f": {body}" if body else "")
        if exc.code in {401, 403}:
            return ModelAuthenticationError(detail)
        if exc.code == 429:
            return ModelRateLimitError(detail)
        if exc.code in {408, 409} or exc.code >= 500:
            return ModelTransientError(detail)
        return ModelResponseError(detail)

    @staticmethod
    def _sleep_before_retry(attempt: int, headers: Message | None = None) -> None:
        retry_after = None
        if headers is not None:
            raw = headers.get("Retry-After")
            try:
                retry_after = float(raw) if raw is not None else None
            except ValueError:
                retry_after = None
        time.sleep(min(5.0, retry_after if retry_after is not None else 0.5 * attempt))

    @staticmethod
    def _validate_response_status(body: dict[str, Any], status: str) -> None:
        if status == "completed":
            return
        if status == "incomplete":
            details = body.get("incomplete_details") or {}
            raise ModelIncompleteError(
                f"Model response incomplete: {details.get('reason', 'unknown')}"
            )
        error = body.get("error") or {}
        raise ModelResponseError(
            f"Model response status '{status}': {error.get('message', 'unknown error')}"
        )

    @staticmethod
    def _extract_output_text(body: dict[str, Any]) -> str:
        if isinstance(body.get("output_text"), str):
            return body["output_text"]
        chunks: list[str] = []
        for item in body.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and isinstance(
                    content.get("text"), str
                ):
                    chunks.append(content["text"])
        if chunks:
            return "".join(chunks)
        raise ModelResponseError("Could not extract output text from Responses API body")

    @staticmethod
    def _extract_chat_output_text(body: dict[str, Any]) -> str:
        message = ModelAdapter._extract_chat_message(body)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelResponseError("DeepSeek response content was empty")
        return content

    @staticmethod
    def _extract_chat_message(body: dict[str, Any]) -> dict[str, Any]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelResponseError("DeepSeek response did not contain choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ModelResponseError("DeepSeek response did not contain a message")
        return message

    @staticmethod
    def _extract_validated_tool_calls(
        message: dict[str, Any], tools_by_name: dict[str, ToolDefinition]
    ) -> list[ModelToolCall]:
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise ModelResponseError("DeepSeek tool_calls must be a list")
        calls: list[ModelToolCall] = []
        seen_ids: set[str] = set()
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict) or raw_call.get("type") != "function":
                raise ModelResponseError("Only function tool calls are supported")
            call_id = raw_call.get("id")
            function = raw_call.get("function")
            if not isinstance(call_id, str) or not call_id:
                raise ModelResponseError("DeepSeek tool call ID must not be empty")
            if call_id in seen_ids:
                raise ModelResponseError(f"Duplicate DeepSeek tool call ID: {call_id}")
            if not isinstance(function, dict):
                raise ModelResponseError("DeepSeek tool call function is missing")
            name = function.get("name")
            raw_arguments = function.get("arguments")
            if not isinstance(name, str) or not isinstance(raw_arguments, str):
                raise ModelResponseError(
                    "DeepSeek tool call requires string name and arguments"
                )
            definition = tools_by_name.get(name)
            if definition is None:
                raise UnknownToolCallError(f"Model requested unknown tool '{name}'")
            arguments = definition.validate_arguments(raw_arguments)
            calls.append(
                ModelToolCall(
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                    raw_arguments=raw_arguments,
                )
            )
            seen_ids.add(call_id)
        return calls

    @staticmethod
    def _extract_chat_finish_reason(body: dict[str, Any]) -> str | None:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return None
        value = choices[0].get("finish_reason")
        return str(value) if value is not None else None

    @staticmethod
    def _validate_chat_finish_reason(
        finish_reason: str | None, *, allow_tool_calls: bool = False
    ) -> None:
        if finish_reason in {None, "stop"} or (
            allow_tool_calls and finish_reason == "tool_calls"
        ):
            return
        if finish_reason == "length":
            raise ModelIncompleteError("DeepSeek response incomplete: finish_reason=length")
        raise ModelResponseError(
            f"DeepSeek response stopped unexpectedly: finish_reason={finish_reason}"
        )

    @staticmethod
    def _extract_usage(
        body: dict[str, Any], prompt: str, text: str
    ) -> tuple[int, int, str]:
        usage = body.get("usage") or {}
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            return input_tokens, output_tokens, "actual"
        return max(1, len(prompt) // 4), max(1, len(text) // 4), "estimated"

    def _notify(
        self,
        call_id: str,
        prompt: str,
        json_schema: dict[str, Any] | None,
        duration_ms: float,
        response: ModelResponse | None,
        error: Exception | None,
    ) -> None:
        if self._observer is None:
            return
        try:
            details: dict[str, Any] = {
                "call_id": call_id,
                "provider": self.provider,
                "model": self.model,
                "prompt_chars": len(prompt),
                "prompt_preview": prompt[:500],
                "structured_output": json_schema is not None,
            }
            if response is not None:
                details.update(
                    {
                        "response_id": response.response_id,
                        "response_status": response.response_status,
                        "finish_reason": response.finish_reason,
                        "input_tokens": response.input_tokens,
                        "input_token_estimate": response.request_input_tokens_estimate,
                        "output_tokens": response.output_tokens,
                        "reserved_output_tokens": response.requested_max_output_tokens,
                        "total_tokens": response.total_tokens,
                        "usage_source": response.usage_source,
                        "cost_usd_estimate": response.cost_usd_estimate,
                        "request_attempts": response.request_attempts,
                        "structured_output_mode": response.structured_output_mode,
                        "output_preview": response.text[:1000],
                        "tool_call_count": len(response.tool_calls),
                        "tool_names": [call.name for call in response.tool_calls],
                    }
                )
            elif error is not None:
                details["request_attempts"] = int(
                    getattr(error, "model_request_attempts", 1) or 1
                )
            self._observer(
                {
                    "event_type": "model_call",
                    "component_type": "model",
                    "component_name": self.model,
                    "agent_name": self._agent_context.get(),
                    "step": "model.complete",
                    "status": "failed" if error else "completed",
                    "duration_ms": duration_ms,
                    "details": details,
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                        "retryable": bool(getattr(error, "retryable", False)),
                    }
                    if error
                    else None,
                }
            )
        except Exception:
            return
