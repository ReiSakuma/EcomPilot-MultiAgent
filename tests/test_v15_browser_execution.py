from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.browser.backends import BrowserBackendError
from app.browser.runtime import get_browser_runtime_status
from app.browser.service import execute_ticketed_plan
from app.browser.tickets import (
    BrowserTicketConsumedError,
    BrowserTicketExpiredError,
    BrowserTicketMismatchError,
    BrowserTicketStore,
)
from app.eval.browser_eval import run_browser_eval
from app.observability.recorder import TraceRecorder
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.state import NodeStatus, TaskNode, TaskState
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.ui import product_detail_html, seller_center_editor_html
from app.tools.browser_tools import browser_execute, browser_verify, reset_seller_center
from app.tools.registry import ToolRegistry
from app.main import TicketedExecutionRequest, seller_center_ui_execute


@pytest.fixture(autouse=True)
def clean_browser_state():
    reset_seller_center()
    yield
    reset_seller_center()


def plan() -> ExecutionPlan:
    return ExecutionPlan(
        operation="update_listing",
        product_id="v15_product",
        title="V15 测试无线耳机",
        bullets=["低延迟", "长续航"],
        price=199,
        stock=800,
        coupon=20,
    )


def test_v15_ticket_is_plan_bound_and_one_time():
    original = plan()
    ticket = BrowserTicketStore.issue(original.model_dump(mode="json"))
    changed = original.model_copy(update={"price": 299.0})

    with pytest.raises(BrowserTicketMismatchError):
        execute_ticketed_plan(ticket, changed)
    result = execute_ticketed_plan(ticket, original)
    assert result["status"] == "applied"
    with pytest.raises(BrowserTicketConsumedError):
        execute_ticketed_plan(ticket, original)


def test_editor_executes_approved_plan_without_round_tripping_provenance():
    approved = ExecutionPlan(
        **plan().model_dump(
            exclude={
                "task_id",
                "run_id",
                "checkpoint_version",
                "source_artifact_hashes",
                "payload_hash",
            }
        ),
        task_id="task_browser_contract",
        run_id="run_browser_contract",
        checkpoint_version=7,
        source_artifact_hashes={"listing": "abc123"},
    )
    ticket = BrowserTicketStore.issue(approved.model_dump(mode="json"))
    editor_plan = ExecutionPlan.model_validate(
        approved.model_dump(
            mode="json",
            exclude={
                "task_id",
                "run_id",
                "checkpoint_version",
                "source_artifact_hashes",
                "payload_hash",
            },
        )
    )

    result = execute_ticketed_plan(ticket, editor_plan)

    assert result["status"] == "applied"
    assert result["verification"]["verified"] is True


def test_editor_cannot_change_an_approved_business_field():
    approved = plan()
    ticket = BrowserTicketStore.issue(approved.model_dump(mode="json"))
    changed = approved.model_copy(update={"stock": 1})

    with pytest.raises(BrowserTicketMismatchError, match="editable plan fields"):
        execute_ticketed_plan(ticket, changed)


def test_seller_center_route_contract_accepts_visible_fields_for_full_approved_plan():
    approved = ExecutionPlan(
        **plan().model_dump(
            exclude={
                "task_id",
                "run_id",
                "checkpoint_version",
                "source_artifact_hashes",
                "payload_hash",
            }
        ),
        task_id="task_http_contract",
        run_id="run_http_contract",
        checkpoint_version=3,
        source_artifact_hashes={"review": "sha256-review"},
    )
    ticket = BrowserTicketStore.issue(approved.model_dump(mode="json"))
    visible_plan = approved.model_dump(
        mode="json",
        include={
            "operation",
            "product_id",
            "title",
            "price",
            "stock",
            "coupon",
            "bullets",
        },
    )

    request = TicketedExecutionRequest.model_validate(
        {"ticket": ticket, "plan": visible_plan}
    )
    response = seller_center_ui_execute(request)

    assert response["status"] == "applied"
    assert response["verification"]["verified"] is True


def test_v15_expired_ticket_is_rejected():
    original = plan()
    ticket = BrowserTicketStore.issue(original.model_dump(mode="json"), ttl_seconds=-1)
    with pytest.raises(BrowserTicketExpiredError):
        execute_ticketed_plan(ticket, original)


def test_v15_mock_backend_keeps_browser_contract():
    original = plan()
    result = browser_execute(original.model_dump(mode="json"), "v15:test:mock")
    verification = browser_verify(original.model_dump(mode="json"))

    assert result["backend"] == "mock"
    assert result["actions"][0]["action"] == "store.apply"
    assert verification["verified"] is True
    assert verification["backend"] == "mock"


def test_v15_browser_runtime_is_explicit_and_safe_by_default():
    status = get_browser_runtime_status()
    assert status["backend"] == "mock"
    assert status["real_browser_enabled"] is False
    assert status["ready"] is True


def test_v15_seller_center_pages_expose_stable_test_ids():
    editor = seller_center_editor_html("ticket-value")
    assert 'data-testid="submit-execution"' in editor
    assert 'data-testid="result-json"' in editor
    assert "ticket-value" in editor
    assert "URLSearchParams" not in editor
    assert "split('\\n')" in editor
    detail = product_detail_html("p1", {"product": None, "promotion": None})
    assert 'data-testid="observed-state"' in detail


def test_v15_browser_eval_passes_default_backend(tmp_path: Path):
    report = run_browser_eval(
        Path("data/eval/v15_browser_cases.json"),
        report_path=tmp_path / "report.json",
    )
    assert report["total"] == 4
    assert report["pass_rate"] == 1.0
    assert json.loads((tmp_path / "report.json").read_text())["passed"] == 4


def test_v15_uncertain_browser_error_suppresses_automatic_node_retry(tmp_path: Path):
    trace = TraceRecorder("run_v15_retry", trace_dir=tmp_path)
    executor = WorkflowExecutor({}, ToolRegistry(), trace)
    node = TaskNode(node_id="browser", agent_name="browser_agent", status=NodeStatus.running)
    state = TaskState(goal="test", status="running", nodes={"browser": node})

    executor._handle_node_error(state, node, BrowserBackendError("ambiguous submit"))

    assert node.status is NodeStatus.failed
    assert node.retry_count == 1
    assert state.status == "needs_attention"
    assert state.needs_attention is True
    events = [json.loads(line) for line in trace.path.read_text().splitlines()]
    assert events[0]["details"]["retry_allowed"] is False
