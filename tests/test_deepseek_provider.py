from __future__ import annotations

import json
import http.client
import os
import ssl
import subprocess
import sys

import pytest

from app.model.adapter import (
    ModelAdapter,
    ModelIncompleteError,
    ModelResponseError,
    ModelTransientError,
)
from app.model.pricing import estimate_cost_usd
from app.model.runtime import get_llm_runtime_status
from app.model.schemas import LISTING_JSON_SCHEMA
from app.orchestration.failures import failure_from_exception
from app.reliability.classifier import classify_failure
from app.reliability.models import FailureTaxonomy
from app.orchestration.workflow import run_workflow


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")


def chat_body(content: str, *, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl_deepseek_test",
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 31, "completion_tokens": 12, "total_tokens": 43},
    }


def test_deepseek_uses_chat_completions_and_json_object(monkeypatch) -> None:
    captured: dict = {}
    content = json.dumps(
        {
            "title": "低延迟无线耳机",
            "keywords": ["无线耳机"],
            "bullets": ["长续航"],
            "compliance_notes": [],
        },
        ensure_ascii=False,
    )

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return FakeResponse(chat_body(content))

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", fake_urlopen)
    adapter = ModelAdapter(
        provider="deepseek",
        model="deepseek-v4-flash",
        api_key="deepseek-test-key",
        base_url="https://api.deepseek.com",
        max_retries=0,
        max_output_tokens=777,
    )
    response = adapter.complete("Return JSON", json_schema=LISTING_JSON_SCHEMA)

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer deepseek-test-key"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["max_tokens"] == 777
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "JSON Schema" in captured["payload"]["messages"][0]["content"]
    assert "required" in captured["payload"]["messages"][0]["content"]
    assert response.response_id == "chatcmpl_deepseek_test"
    assert response.finish_reason == "stop"
    assert response.usage_source == "actual"
    assert response.total_tokens == 43
    assert response.structured_output_mode == "json_object_local_schema"


def test_deepseek_plain_completion_does_not_force_json_mode(monkeypatch) -> None:
    captured: dict = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(chat_body("plain text"))

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", fake_urlopen)
    response = ModelAdapter(
        provider="deepseek", api_key="test", max_retries=0
    ).complete("hello")

    assert "response_format" not in captured
    assert response.text == "plain text"
    assert response.structured_output_mode == "none"


def test_deepseek_retries_remote_disconnect_and_reports_attempts(monkeypatch) -> None:
    calls = 0

    def flaky_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            )
        return FakeResponse(chat_body("连接恢复"))

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", flaky_urlopen)
    adapter = ModelAdapter(provider="deepseek", api_key="test", max_retries=1)

    response = adapter.complete("hello")

    assert calls == 2
    assert response.text == "连接恢复"
    assert response.request_attempts == 2


def test_deepseek_exhausted_tls_retry_returns_network_failure(monkeypatch) -> None:
    calls = 0

    def unavailable(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ssl.SSLError("SSLV3_ALERT_BAD_RECORD_MAC")

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", unavailable)
    adapter = ModelAdapter(provider="deepseek", api_key="test", max_retries=1)

    with pytest.raises(ModelTransientError) as captured:
        adapter.complete("hello")

    error = captured.value
    failure = failure_from_exception(
        error, stage="listing", agent_name="listing_agent"
    )
    assert calls == 2
    assert error.safe_to_retry is True
    assert getattr(error, "model_request_attempts") == 2
    assert classify_failure(error) is FailureTaxonomy.transient
    assert failure.code == "model_network_unavailable"
    assert failure.retry_action == "retry_stage"
    assert failure.transport_attempts == 2
    assert "网络连接暂时中断" in failure.user_message
    assert "自动尝试 2 次" in failure.user_message


def test_deepseek_length_finish_reason_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.model.adapter.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(chat_body("{}", finish_reason="length")),
    )
    adapter = ModelAdapter(provider="deepseek", api_key="test", max_retries=0)

    with pytest.raises(ModelIncompleteError, match="finish_reason=length"):
        adapter.complete("return JSON", json_schema=LISTING_JSON_SCHEMA)


def test_deepseek_empty_content_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.model.adapter.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(chat_body("")),
    )
    adapter = ModelAdapter(provider="deepseek", api_key="test", max_retries=0)

    with pytest.raises(ModelResponseError, match="content was empty"):
        adapter.complete("return JSON", json_schema=LISTING_JSON_SCHEMA)


def test_deepseek_runs_all_three_agents_with_local_schema_validation(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ECOMPILOT_LLM_AGENTS", "listing_agent,strategy_agent,review_agent"
    )
    monkeypatch.setenv("ECOMPILOT_LLM_FALLBACK", "fail_closed")
    monkeypatch.setattr("app.agents.supervisor.LLM_PROVIDER", "deepseek")
    monkeypatch.setattr("app.agents.supervisor.LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr("app.model.adapter.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.model.adapter.LLM_BASE_URL", "https://api.deepseek.com")

    def fake_urlopen(request, timeout):
        prompt = json.loads(request.data.decode("utf-8"))["messages"][0]["content"]
        if "Listing Agent" in prompt:
            content = {
                "title": "低延迟长续航无线耳机",
                "keywords": ["无线耳机", "低延迟耳机"],
                "bullets": ["长续航", "清晰通话"],
                "compliance_notes": ["未使用绝对化词"],
            }
        elif "ecommerce launch strategy" in prompt:
            content = {
                "launch_plan": "首月分阶段投放学生市场",
                "rationale": "遵守工具计算结果",
                "discount_amount_yuan": 10,
            }
        else:
            content = {"issues": []}
        return FakeResponse(chat_body(json.dumps(content, ensure_ascii=False)))

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", fake_urlopen)
    state = run_workflow(GOAL, approved=False)

    assert state.status == "waiting_for_approval"
    assert len(state.model_records) == 3
    assert all(record["provider"] == "deepseek" for record in state.model_records)
    assert all(
        record["structured_validation"] == "passed" for record in state.model_records
    )
    assert all(
        record["structured_output_mode"] == "json_object_local_schema"
        for record in state.model_records
    )
    assert not state.model_fallbacks


def test_runtime_accepts_deepseek_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setattr("app.model.runtime.LLM_PROVIDER", "deepseek")
    monkeypatch.setattr("app.model.runtime.LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr("app.model.runtime.LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr("app.model.runtime.LLM_API_KEY", "secret-deepseek-key")
    monkeypatch.setenv("ECOMPILOT_LLM_AGENTS", "listing_agent")

    status = get_llm_runtime_status()

    assert status["ready"] is True
    assert status["real_llm_enabled"] is True
    assert status["provider"] == "deepseek"
    assert "secret-deepseek-key" not in json.dumps(status)


def test_config_reads_deepseek_api_key_in_fresh_process() -> None:
    environment = os.environ.copy()
    for name in ("ECOMPILOT_LLM_API_KEY", "OPENAI_API_KEY"):
        environment.pop(name, None)
    environment.update(
        {
            "ECOMPILOT_LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "fresh-process-secret",
        }
    )
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from app.config import LLM_API_KEY, LLM_BASE_URL; "
            "print(LLM_API_KEY == 'fresh-process-secret', LLM_BASE_URL)",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=environment,
        text=True,
    )
    assert output.strip() == "True https://api.deepseek.com"


def test_deepseek_does_not_reuse_an_openai_key_in_fresh_process() -> None:
    environment = os.environ.copy()
    for name in ("ECOMPILOT_LLM_API_KEY", "DEEPSEEK_API_KEY"):
        environment.pop(name, None)
    environment.update(
        {
            "ECOMPILOT_LLM_PROVIDER": "deepseek",
            "OPENAI_API_KEY": "wrong-provider-secret",
        }
    )
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from app.config import LLM_API_KEY; print(LLM_API_KEY is None)",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=environment,
        text=True,
    )
    assert output.strip() == "True"


def test_deepseek_pricing_is_nonzero_for_supported_models() -> None:
    assert estimate_cost_usd("deepseek-v4-flash", 1_000_000, 1_000_000) == 0.42
    assert estimate_cost_usd("deepseek-v4-pro", 1_000_000, 1_000_000) == 1.305
