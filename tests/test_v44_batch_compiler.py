from __future__ import annotations

from pathlib import Path

import pytest

from app.access.models import default_principal
from app.conversations.repository import (
    ConversationNotFoundError,
    ConversationRepository,
)
from app.copilot.compiler import RequestCompiler
from app.copilot.facade import ConversationFacade
from app.copilot.intents import RequestMode
from app.copilot.schemas import CopilotOutcome
from app.model.adapter import ModelAdapter


MESSAGE = (
    "帮我同时上架无线耳机和机械键盘。"
    "耳机成本95元售价300元，键盘成本120元售价260元，"
    "库存都是800件，最低毛利率都不低于20%。"
)


def _compiler() -> RequestCompiler:
    return RequestCompiler(ModelAdapter(provider="deterministic", model="local-rule-v6"))


def test_multi_product_values_are_split_instead_of_reported_as_conflicts() -> None:
    compiled = _compiler().compile(MESSAGE)

    assert compiled.compiler_protocol_version == "1.3"
    assert compiled.batch_plan is not None
    assert compiled.batch_plan.status == "needs_confirmation"
    assert compiled.conflicts == []
    assert [item.structured_request["target_price"] for item in compiled.batch_plan.items] == [
        300.0,
        260.0,
    ]
    assert [item.structured_request["cost"] for item in compiled.batch_plan.items] == [
        95.0,
        120.0,
    ]
    assert all(item.structured_request["inventory"] == 800 for item in compiled.batch_plan.items)
    assert all(item.structured_request["min_margin_rate"] == 0.2 for item in compiled.batch_plan.items)
    assert all(item.assessment.mode is RequestMode.execute for item in compiled.batch_plan.items)


def test_one_incomplete_item_does_not_erase_the_valid_item() -> None:
    compiled = _compiler().compile(
        "帮我同时上架耳机和键盘。耳机成本95元售价300元，"
        "键盘成本120元，库存都是800件。"
    )

    assert compiled.batch_plan is not None
    assert compiled.batch_plan.status == "needs_clarification"
    earphone, keyboard = compiled.batch_plan.items
    assert earphone.assessment.mode is RequestMode.execute
    assert keyboard.assessment.mode is RequestMode.clarify
    assert keyboard.assessment.missing_fields == ["target_price"]
    assert "键盘" in (compiled.assessment.clarification_question or "")

    confirmed = _compiler().compile("确认批次", existing=compiled, clarification_round=1)
    assert confirmed.batch_plan.status == "needs_clarification"
    assert confirmed.assessment.mode is RequestMode.clarify
    assert confirmed.decision.intent.value == "clarify"


def test_facade_materializes_one_batch_item_per_task_session(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    response = ConversationFacade(repository=repository).handle_message(
        MESSAGE,
        principal=default_principal(),
        client_request_id="req_v44_batch",
    )
    detail = repository.get_detail("tenant_demo", response.conversation_id)

    assert response.outcome is CopilotOutcome.waiting_for_input
    assert len(detail.batch_jobs) == 1
    assert detail.batch_jobs[0].item_count == 2
    assert len(detail.batch_items) == 2
    assert len({item.task_session_id for item in detail.batch_items}) == 2
    assert all(item.task_session_id != response.thread_id for item in detail.batch_items)
    assert {item.request_payload["target_price"] for item in detail.batch_items} == {260.0, 300.0}


def test_batch_materialization_is_idempotent_and_tenant_scoped(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.db")
    conversation = repository.create_conversation("tenant_alpha", title="batch")
    turn = repository.begin_turn(
        "tenant_alpha", conversation.conversation_id,
        client_request_id="req_batch_idempotent", message=MESSAGE,
    ).turn
    compiled = _compiler().compile(MESSAGE)
    payload = [
        {
            "item_id": item.item_id,
            "label": item.label,
            "structured_request": item.structured_request,
            "status": "ready",
        }
        for item in compiled.batch_plan.items
    ]
    first, first_items = repository.materialize_batch_plan(
        "tenant_alpha", conversation.conversation_id, turn.turn_id,
        operation="create_listing", items=payload,
    )
    second, second_items = repository.materialize_batch_plan(
        "tenant_alpha", conversation.conversation_id, turn.turn_id,
        operation="create_listing", items=payload,
    )

    assert first.batch_job_id == second.batch_job_id
    assert [item.task_session_id for item in first_items] == [
        item.task_session_id for item in second_items
    ]
    with pytest.raises(ConversationNotFoundError):
        repository.list_batch_items("tenant_beta", first.batch_job_id)
