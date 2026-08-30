from __future__ import annotations

from pathlib import Path
import time
from threading import Event

import pytest

from app.conversations.repository import ConversationRepository
from app.copilot.events import CopilotEventStore
from app.copilot.facade import ConversationFacade
from app.public_progress import bind_public_event_sink, project_trace_event
from app.copilot.schemas import CopilotResponse
from app.copilot_ui import COPILOT_HTML
from app.observability.recorder import TraceRecorder
from app.observability.schemas import TraceEventType
from app.orchestration.workflow import run_workflow


GOAL = (
    "我要上架一款成本95元、售价300元、库存800件的无线耳机，"
    "最低毛利率40%，面向游戏爱好者。"
)


def _response() -> CopilotResponse:
    return ConversationFacade.build_response(run_workflow(GOAL, approved=False))


def test_v34_response_binds_approval_to_execution_plan_hash() -> None:
    response = _response()

    assert response.protocol_version == "1.7"
    assert response.approval_required is True
    assert response.approval_state == "waiting"
    assert response.execution_plan_hash is not None
    assert len(response.execution_plan_hash) == 64

    changed = response.model_copy(deep=True)
    strategy = next(panel for panel in changed.panels if panel.panel_id == "strategy")
    strategy.data["coupon"] = 99
    changed.execution_plan_hash = None
    rebuilt = CopilotResponse.model_validate(changed.model_dump(mode="json"))

    assert rebuilt.execution_plan_hash != response.execution_plan_hash


def test_v34_event_stream_is_persistent_resume_safe_and_tenant_scoped(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.db"
    repository = ConversationRepository(database)
    conversation = repository.create_conversation("tenant_a", title="stream")
    store = CopilotEventStore(database)
    stream_id = store.create("tenant_a", conversation.conversation_id)
    store.complete("tenant_a", stream_id, _response())

    first_page = store.events_after("tenant_a", stream_id, 0)
    resumed = store.events_after("tenant_a", stream_id, first_page[1].event_id)

    assert first_page[0].event_type == "request_received"
    assert first_page[-1].event_type == "response_ready"
    assert resumed[0].event_id > first_page[1].event_id
    assert store.status("tenant_a", stream_id) == "completed"
    assert all("reasoning" not in event.payload for event in first_page)
    with pytest.raises(KeyError):
        store.status("tenant_b", stream_id)


def test_v34_conversation_search_product_and_pending_filters(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "filters.db")
    headphones = repository.create_conversation("tenant_a", title="无线耳机上新")
    keyboards = repository.create_conversation("tenant_a", title="机械键盘调研")
    repository.set_active_product("tenant_a", headphones.conversation_id, "product_headphone")
    reservation = repository.begin_turn(
        "tenant_a", headphones.conversation_id,
        client_request_id="req_filter_pending", message=GOAL,
    )
    state = run_workflow(GOAL, approved=False)
    state.principal = state.principal.model_copy(update={"tenant_id": "tenant_a"})
    state.conversation_id = headphones.conversation_id
    state.turn_id = reservation.turn.turn_id
    repository.complete_turn(state, "方案等待确认。")

    assert [item.conversation_id for item in repository.list_conversations(
        "tenant_a", query="耳机"
    )] == [headphones.conversation_id]
    assert [item.conversation_id for item in repository.list_conversations(
        "tenant_a", product_id="product_headphone"
    )] == [headphones.conversation_id]
    assert [item.conversation_id for item in repository.list_conversations(
        "tenant_a", approval_status="pending"
    )] == [headphones.conversation_id]
    assert keyboards.conversation_id not in {
        item.conversation_id for item in repository.list_conversations(
            "tenant_a", approval_status="pending"
        )
    }


def test_v34_ui_uses_sse_dynamic_panels_and_mobile_dual_view() -> None:
    assert "/api/copilot/messages/dispatch" in COPILOT_HTML
    assert "new EventSource" in COPILOT_HTML
    assert "data-mobile-mode=\"chat\"" in COPILOT_HTML
    assert "data-mobile-mode=\"results\"" in COPILOT_HTML
    assert "historySearch" in COPILOT_HTML
    assert "productFilter" in COPILOT_HTML
    assert "approvalFilter" in COPILOT_HTML
    assert "execution_plan_hash:currentResponse.execution_plan_hash" in COPILOT_HTML
    assert "/active-stream" in COPILOT_HTML
    assert "本次真实模型" in COPILOT_HTML
    assert "conversation_id=" in COPILOT_HTML
    assert "Raw JSON" not in COPILOT_HTML


def test_v34_dispatch_api_runs_in_background_and_persists_terminal_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as main_module

    repository = ConversationRepository(tmp_path / "api_events.db")
    event_store = CopilotEventStore(repository.database_path)
    expected = _response()

    class FakeFacade:
        def handle_message(self, *_args, **_kwargs):
            return expected

    monkeypatch.setattr(main_module, "require_linked_runtime", lambda: None)
    monkeypatch.setattr(main_module, "ConversationRepository", lambda: repository)
    monkeypatch.setattr(main_module, "CopilotEventStore", lambda: event_store)
    monkeypatch.setattr(main_module, "ConversationFacade", FakeFacade)

    dispatched = main_module.dispatch_copilot_message(
        main_module.CopilotMessageRequest(
            message=GOAL, client_request_id="request_v34_dispatch"
        )
    )
    stream_id = dispatched.stream_id
    for _ in range(100):
        if event_store.status("tenant_demo", stream_id) != "running":
            break
        time.sleep(0.01)

    events = event_store.events_after("tenant_demo", stream_id)
    assert dispatched.events_url.endswith(f"/{stream_id}/events")
    assert events[0].event_type == "request_received"
    assert events[-1].event_type == "response_ready"
    assert events[-1].payload["response"]["response_id"] == expected.response_id


def test_v34_execution_events_are_visible_before_terminal_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as main_module

    repository = ConversationRepository(tmp_path / "live_events.db")
    event_store = CopilotEventStore(repository.database_path)
    expected = _response()
    progress_written = Event()
    release_task = Event()

    class LiveFacade:
        def handle_message(self, *_args, **_kwargs):
            recorder = TraceRecorder("run_live_v34", tmp_path / "traces")
            recorder.record_event(
                "task_live_v34", TraceEventType.plan_created,
                "orchestrator", "supervisor", "plan", status="completed",
                details={"hidden_prompt": "must not reach the public stream"},
            )
            recorder.record_event(
                "task_live_v34", TraceEventType.node_started,
                "agent", "market_agent", "market", status="running",
                details={"sql": "SELECT secret FROM private_table"},
            )
            progress_written.set()
            assert release_task.wait(timeout=2)
            return expected

    monkeypatch.setattr(main_module, "require_linked_runtime", lambda: None)
    monkeypatch.setattr(main_module, "ConversationRepository", lambda: repository)
    monkeypatch.setattr(main_module, "CopilotEventStore", lambda: event_store)
    monkeypatch.setattr(main_module, "ConversationFacade", LiveFacade)

    dispatched = main_module.dispatch_copilot_message(
        main_module.CopilotMessageRequest(
            message=GOAL, client_request_id="request_v34_live"
        )
    )
    assert progress_written.wait(timeout=2)

    running_events = event_store.events_after("tenant_demo", dispatched.stream_id)
    assert event_store.status("tenant_demo", dispatched.stream_id) == "running"
    assert [event.event_type for event in running_events] == [
        "request_received", "route_planned", "agent_started"
    ]
    assert event_store.active_stream(
        "tenant_demo", dispatched.conversation_id
    )["stream_id"] == dispatched.stream_id
    public_payload = " ".join(
        event.model_dump_json() for event in running_events
    )
    assert "private_table" not in public_payload
    assert "hidden_prompt" not in public_payload

    release_task.set()
    for _ in range(100):
        if event_store.status("tenant_demo", dispatched.stream_id) == "completed":
            break
        time.sleep(0.01)
    assert event_store.status("tenant_demo", dispatched.stream_id) == "completed"
    assert event_store.active_stream("tenant_demo", dispatched.conversation_id) is None


def test_v34_public_trace_projection_is_allowlist_based() -> None:
    projected = project_trace_event({
        "event_id": "evt_safe",
        "task_id": "task_safe",
        "event_type": "tool_call",
        "component_name": "query_market_database",
        "status": "completed",
        "details": {
            "prompt": "hidden prompt",
            "sql": "SELECT * FROM products",
            "result": {"secret": "hidden result"},
        },
    })

    assert projected is not None
    assert projected["event_type"] == "tool_completed"
    assert projected["payload"] == {"trace_ref": "evt_safe"}
    assert "SELECT" not in str(projected)
    assert "hidden" not in str(projected)


def test_v34_progress_context_reaches_agent_and_tool_worker_threads() -> None:
    projected: list[dict] = []

    with bind_public_event_sink(projected.append):
        run_workflow(GOAL, approved=False)

    event_types = {event["event_type"] for event in projected}
    assert "agent_started" in event_types
    assert "agent_completed" in event_types
    assert "tool_completed" in event_types
