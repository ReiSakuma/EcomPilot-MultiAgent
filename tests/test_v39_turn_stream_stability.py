from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.access.models import default_principal
from app.agents.strategy import StrategyAgent
from app.conversations.repository import ConversationRepository
from app.copilot.events import CopilotEventStore
from app.copilot.facade import ConversationFacade
from app.copilot.intents import CreateListingRequest, ModifyListingRequest
from app.copilot_ui import COPILOT_HTML
from app.main import _public_copilot_error
from app.model.contracts import StrategyModelOutput
from app.model.policy import LlmPolicy
from app.orchestration.planner import Planner
from app.tools.registry import ToolRegistry


def _validation_error(model, payload: dict) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    return caught.value


def test_public_validation_message_only_labels_real_listing_input_errors() -> None:
    input_error = _validation_error(
        CreateListingRequest,
        {"target_price": 0, "cost": 95, "inventory": 800, "min_margin_rate": 0.4},
    )
    model_error = _validation_error(StrategyModelOutput, {})

    assert "价格、库存、成本或毛利率" in _public_copilot_error(input_error)
    assert _public_copilot_error(model_error) == (
        "本次请求没有完成。系统已记录技术信息，请稍后重试。"
    )


def test_empty_modify_request_has_an_actionable_public_message() -> None:
    modify_error = _validation_error(
        ModifyListingRequest,
        {"query": "修改商品", "changes": []},
    )

    assert "没有识别到可执行的商品修改项" in _public_copilot_error(modify_error)


def test_stream_failure_is_terminal_and_idempotent(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "events.db")
    conversation = repository.create_conversation("tenant_demo", title="stream")
    store = CopilotEventStore(repository.database_path)
    stream_id = store.create("tenant_demo", conversation.conversation_id)

    store.fail("tenant_demo", stream_id, "first failure")
    store.fail("tenant_demo", stream_id, "duplicate failure")

    failures = [
        event
        for event in store.events_after("tenant_demo", stream_id)
        if event.event_type == "stream_failed"
    ]
    assert len(failures) == 1
    assert failures[0].detail == "first failure"


def test_pending_clarification_is_consumed_before_resume_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.copilot.graph as graph_module

    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_demo", title="pending")
    repository.save_pending_request(
        "tenant_demo",
        conversation.conversation_id,
        compiled_payload={"old": "request"},
        clarification_round=1,
        last_question="请补充",
    )

    class FailingGraph:
        def __init__(self, *_args, **_kwargs):
            pass

        def resume(self, *_args, **_kwargs):
            assert repository.get_pending_request(
                "tenant_demo", conversation.conversation_id
            ) is None
            raise RuntimeError("downstream failed")

    monkeypatch.setattr(graph_module, "V33ConversationGraph", FailingGraph)

    with pytest.raises(RuntimeError, match="downstream failed"):
        ConversationFacade(repository=repository).handle_message(
            "继续",
            principal=default_principal(),
            conversation_id=conversation.conversation_id,
            client_request_id="req_pending_failure",
        )
    assert repository.get_pending_request(
        "tenant_demo", conversation.conversation_id
    ) is None


def test_legacy_pending_request_after_failed_reply_is_self_healed(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "legacy_pending.db")
    conversation = repository.create_conversation("tenant_demo", title="pending")
    repository.save_pending_request(
        "tenant_demo",
        conversation.conversation_id,
        compiled_payload={"old": "request"},
        clarification_round=1,
        last_question="请补充",
    )
    turn = repository.begin_turn(
        "tenant_demo",
        conversation.conversation_id,
        client_request_id="req_failed_reply",
        message="继续",
    ).turn
    repository.fail_turn("tenant_demo", turn.turn_id, "ValidationError")

    assert repository.get_pending_request(
        "tenant_demo", conversation.conversation_id
    ) is None


def test_strategy_invalid_model_json_degrades_to_verified_core() -> None:
    policy = LlmPolicy(
        enabled_agents={"strategy_agent"},
        react_enabled_agents={"strategy_agent"},
        fallback_mode="fail_closed",
    )
    registry = ToolRegistry()
    agent = StrategyAgent(registry, llm_policy=policy, react_loop=object())
    validation_error = _validation_error(StrategyModelOutput, {})

    def fail_react(*_args, **_kwargs):
        raise validation_error

    agent._run_react = fail_react  # type: ignore[method-assign]
    state = Planner().build_initial_state(
        "我要上架成本95元、售价300元、库存800件的无线耳机，毛利率不低于40%。"
    )

    with registry.agent_scope(
        "strategy_agent", task_id=state.task_id, tenant_id=state.principal.tenant_id
    ):
        result = agent.run(state).result

    assert result["generation_mode"] == "deterministic_fallback"
    assert result["margin"]["margin_rate"] >= 0.4
    assert result["inventory_check"]["valid"] is True
    assert state.model_fallbacks[-1]["fallback"] == "deterministic_verified_strategy"


def test_user_ui_keeps_stream_errors_in_the_conversation() -> None:
    assert "showError(event.detail)" not in COPILOT_HTML
    assert "activeEventSource !== source" in COPILOT_HTML
    assert "showWorkspaceProgress();" in COPILOT_HTML
    assert "addMessage('assistant', event.detail);" in COPILOT_HTML
