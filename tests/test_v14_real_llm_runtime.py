from __future__ import annotations

import io
import json
import time
import urllib.error
from email.message import Message

import pytest

from app.context.schemas import ContextPackage
from app.eval.llm_eval import run_llm_eval
from app.model.adapter import (
    ModelAdapter,
    ModelAuthenticationError,
    ModelIncompleteError,
)
from app.model.contracts import CoreReviewOutput
from app.model.prompts import review_prompt
from app.model.runtime import get_llm_runtime_status
from app.model.schemas import LISTING_JSON_SCHEMA
from app.observability.recorder import TraceRecorder
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.handoff import Handoff
from app.orchestration.state import NodeStatus, TaskNode, TaskState
from app.orchestration.workflow import run_workflow
from app.tools.registry import ToolRegistry


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


def completed_body(text: str = '{"ok":true}') -> dict:
    return {
        "id": "resp_v14_test",
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 21, "output_tokens": 8, "total_tokens": 29},
    }


def test_v14_openai_request_uses_strict_responses_schema(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(completed_body())

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", fake_urlopen)
    adapter = ModelAdapter(
        provider="openai",
        model="gpt-5-mini",
        api_key="test-key",
        max_retries=0,
        max_output_tokens=777,
    )
    response = adapter.complete("return json", json_schema=LISTING_JSON_SCHEMA)

    assert captured["payload"]["store"] is False
    assert captured["payload"]["max_output_tokens"] == 777
    assert captured["payload"]["text"]["format"]["type"] == "json_schema"
    assert captured["payload"]["text"]["format"]["strict"] is True
    assert response.response_id == "resp_v14_test"
    assert response.usage_source == "actual"
    assert response.total_tokens == 29


def test_v14_authentication_failure_is_not_retried(monkeypatch):
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            "https://api.openai.com/v1/responses",
            401,
            "unauthorized",
            Message(),
            io.BytesIO(b'{"error":{"message":"bad key"}}'),
        )

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", fake_urlopen)
    adapter = ModelAdapter(provider="openai", api_key="bad-key", max_retries=2)

    with pytest.raises(ModelAuthenticationError):
        adapter.complete("hello")
    assert calls == 1


def test_v14_rate_limit_retries_then_records_attempt_count(monkeypatch):
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://api.openai.com/v1/responses",
                429,
                "rate limit",
                Message(),
                io.BytesIO(b'{"error":{"message":"slow down"}}'),
            )
        return FakeResponse(completed_body())

    monkeypatch.setattr("app.model.adapter.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.model.adapter.time.sleep", lambda _seconds: None)
    adapter = ModelAdapter(provider="openai", api_key="test-key", max_retries=1)

    response = adapter.complete("hello")
    assert response.request_attempts == 2
    assert calls == 2


def test_v14_incomplete_response_fails_closed_at_adapter(monkeypatch):
    body = {
        "id": "resp_incomplete",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [],
    }
    monkeypatch.setattr(
        "app.model.adapter.urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse(body)
    )
    adapter = ModelAdapter(provider="openai", api_key="test-key", max_retries=0)

    with pytest.raises(ModelIncompleteError, match="max_output_tokens"):
        adapter.complete("hello")


def test_v14_missing_key_falls_back_and_is_audited(monkeypatch):
    monkeypatch.setenv("ECOMPILOT_LLM_AGENTS", "listing_agent")
    monkeypatch.setenv("ECOMPILOT_LLM_FALLBACK", "deterministic")
    monkeypatch.setattr("app.agents.supervisor.LLM_PROVIDER", "openai")
    monkeypatch.setattr("app.agents.supervisor.LLM_MODEL", "gpt-5-mini")
    monkeypatch.setattr("app.model.adapter.LLM_API_KEY", None)

    state = run_workflow(GOAL, approved=False)

    assert state.agent_outputs["listing_agent"]["generation_mode"] == "deterministic_fallback"
    assert state.model_records[0]["status"] == "failed"
    assert state.model_fallbacks[0]["error_type"] == "ModelConfigurationError"


def test_v14_runtime_status_never_exposes_key(monkeypatch):
    monkeypatch.setattr("app.model.runtime.LLM_PROVIDER", "openai")
    monkeypatch.setattr("app.model.runtime.LLM_API_KEY", "secret-value")
    monkeypatch.setenv("ECOMPILOT_LLM_AGENTS", "listing_agent")

    status = get_llm_runtime_status()

    assert status["ready"] is True
    assert status["api_key_configured"] is True
    assert "secret-value" not in json.dumps(status)


def test_v14_llm_eval_exercises_all_enabled_agents(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "ECOMPILOT_LLM_AGENTS", "listing_agent,strategy_agent,review_agent"
    )
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps([{"case_id": "one", "goal": GOAL}]), encoding="utf-8")
    report = run_llm_eval(tasks, report_path=tmp_path / "report.json")

    assert report["model_call_count"] == 3
    assert report["case_structured_success_rate"] == 1.0
    assert report["fallback_count"] == 0


class SlowReviewAgent:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.model_adapter = ModelAdapter()

    def run(self, state: TaskState) -> Handoff:
        self.calls += 1
        time.sleep(self.delay_seconds)
        return Handoff(
            task_id=state.task_id,
            source_agent="review_agent",
            target_agent="browser_agent",
            result={"ok": True},
        )


def make_timeout_executor(tmp_path, agent: SlowReviewAgent, timeout: float):
    trace = TraceRecorder("run_timeout_test", trace_dir=tmp_path / "traces")
    executor = WorkflowExecutor(
        {"review_agent": agent},
        ToolRegistry(),
        trace,
        checkpoint_store=CheckpointStore(tmp_path / "checkpoints"),
        node_timeout_seconds=timeout,
    )
    state = TaskState(goal="timeout test", run_id="run_timeout_test", status="running")
    node = TaskNode(
        node_id="review",
        agent_name="review_agent",
        status=NodeStatus.running,
        max_retries=1,
    )
    return executor, state, node, trace


def test_v14_node_timeout_never_retries_an_inflight_agent(tmp_path):
    agent = SlowReviewAgent(delay_seconds=0.12)
    executor, state, node, trace = make_timeout_executor(tmp_path, agent, timeout=0.02)

    executor._run_one(state, node)
    time.sleep(0.15)

    assert agent.calls == 1
    assert state.status == "failed"
    assert node.status is NodeStatus.failed
    assert node.retry_count == 1
    assert state.handoffs == []
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    timeout_event = next(event for event in events if event["event_type"] == "error")
    assert timeout_event["error"]["type"] == "timeout"
    assert timeout_event["details"]["retry_allowed"] is False


def test_v14_node_accepts_a_slow_result_within_configured_budget(tmp_path):
    agent = SlowReviewAgent(delay_seconds=0.03)
    executor, state, node, _trace = make_timeout_executor(tmp_path, agent, timeout=0.2)

    executor._run_one(state, node)

    assert agent.calls == 1
    assert node.status is NodeStatus.completed
    assert state.agent_outputs["review_agent"] == {"ok": True}


def test_v14_review_output_and_prompt_are_bounded():
    schema = CoreReviewOutput.model_json_schema()
    context = ContextPackage(
        agent_name="review_agent", task_summary="test context", token_estimate=3
    )

    assert schema["properties"]["issues"]["maxItems"] == 3
    assert schema["$defs"]["CoreReviewIssue"]["properties"]["message"]["maxLength"] == 80
    assert "最多3条" in review_prompt(context)
    assert "不要解释" in review_prompt(context)


def test_v14_runtime_exposes_coordinated_node_budget():
    status = get_llm_runtime_status()

    assert status["node_timeout_seconds"] > status["llm_request_budget_seconds"]
