from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.access.models import default_principal
from app.conversations.repository import ConversationRepository
from app.copilot.graph import V30ConversationGraph
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.orchestration.workflow import run_workflow
from app.products.ledger import ProductLedger
from app.products.resolver import EntityResolver


GOAL = (
    "我要上架一款成本95元的无线耳机，售价199元，库存800件，"
    "毛利率不低于40%。已确认产品功能：蓝牙5.3、游戏低延迟。"
)


def _record_product(
    repository: ConversationRepository,
    conversation_id: str,
    *,
    goal: str = GOAL,
):
    state = run_workflow(goal, approved=True)
    state.conversation_id = conversation_id
    state.turn_id = f"turn_{state.task_id}"
    product = ProductLedger(repository.database_path).record_successful_execution(state)
    return state, product


def test_v30_execution_builds_bidirectional_product_identity_and_timeline(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "conversation.db")
    conversation = repository.create_conversation("tenant_demo")
    state, product = _record_product(repository, conversation.conversation_id)
    ledger = ProductLedger(repository.database_path)
    detail = ledger.detail("tenant_demo", product.product_id)

    assert product.product_id == f"product_{state.task_id.removeprefix('task_')}"
    assert ledger.product_for_task("tenant_demo", state.task_id) == product
    assert detail.task_links[0].artifact_refs
    assert {event.event_type for event in detail.timeline} >= {
        "listing_created",
        "reviewed",
        "store_synced",
    }
    assert repository.get_conversation(
        "tenant_demo", conversation.conversation_id
    ).active_product_id == product.product_id
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 13


def test_v30_resolver_prefers_structural_ids_and_hides_other_tenants_and_deleted(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "conversation.db")
    conversation = repository.create_conversation("tenant_demo")
    _state, product = _record_product(repository, conversation.conversation_id)
    ledger = ProductLedger(repository.database_path)
    resolver = EntityResolver(ledger, repository)

    assert resolver.resolve("tenant_demo", product.product_id).product_id == product.product_id
    assert resolver.resolve("tenant_demo", product.sku or "").product_id == product.product_id
    assert resolver.resolve("tenant_beta", product.product_id).status == "not_found"

    ledger.mark_deleted("tenant_demo", product.product_id)
    assert resolver.resolve("tenant_demo", product.product_id).status == "not_found"
    assert resolver.resolve(
        "tenant_demo", "查看这个商品详情", conversation_id=conversation.conversation_id
    ).status == "not_found"


def test_v30_context_reference_resolves_one_product_and_graph_returns_timeline(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "conversation.db")
    conversation = repository.create_conversation("tenant_demo")
    _state, product = _record_product(repository, conversation.conversation_id)
    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    checkpointer.setup()
    graph = V30ConversationGraph(repository=repository, checkpointer=checkpointer)

    response, steps, compiled = graph.invoke(
        "查看这个商品详情",
        principal=default_principal(),
        conversation_id=conversation.conversation_id,
        turn_id="turn_product_detail",
    )

    assert compiled.decision.intent.value == "product_detail"
    assert response.outcome is CopilotOutcome.answered
    assert response.entity_refs == [product.product_id]
    assert {panel.panel_id for panel in response.panels} == {"product", "timeline"}
    assert "product_detail" in steps


def test_v30_multiple_candidates_always_interrupt_for_user_selection(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "conversation.db")
    conversation = repository.create_conversation("tenant_demo")
    _record_product(repository, conversation.conversation_id)
    _record_product(
        repository,
        conversation.conversation_id,
            goal="我要上架一款成本80元的无线耳机，售价209元，库存600件，毛利率不低于35%。",
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE conversations SET active_product_id=NULL WHERE conversation_id=?",
            (conversation.conversation_id,),
        )
    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    checkpointer.setup()
    graph = V30ConversationGraph(repository=repository, checkpointer=checkpointer)

    waiting, _steps, compiled = graph.invoke(
        "查看无线耳机商品详情",
        principal=default_principal(),
        conversation_id=conversation.conversation_id,
        turn_id="turn_ambiguous",
    )

    assert waiting.outcome is CopilotOutcome.waiting_for_input
    assert compiled.decision.original_intent.value == "product_detail"
    assert "1." in waiting.assistant_message and "2." in waiting.assistant_message

    resolved, _steps, compiled = graph.resume(
        "2",
        conversation_id=conversation.conversation_id,
        turn_id="turn_selected",
    )
    assert resolved.outcome is CopilotOutcome.answered
    assert compiled.decision.intent.value == "product_detail"
    assert len(resolved.entity_refs) == 1


def test_v30_facade_approval_and_follow_up_share_product_identity(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(tmp_path / "conversation.db")
    facade = ConversationFacade(repository=repository)
    principal = default_principal()

    planned = facade.handle_message(
        GOAL,
        principal=principal,
        client_request_id="req_v30_plan",
    )
    executed = facade.approve(planned.task_id or "", principal=principal)
    follow_up = facade.handle_message(
        "查看这个商品详情",
        principal=principal,
        conversation_id=planned.conversation_id,
        client_request_id="req_v30_detail",
    )

    assert executed.outcome is CopilotOutcome.completed
    assert executed.entity_refs
    assert {panel.panel_id for panel in executed.panels} >= {"product", "timeline"}
    assert follow_up.entity_refs == executed.entity_refs
    assert follow_up.store_modified is False
    assert follow_up.action_summary.tool_call_count == 0
    detail = repository.get_detail("tenant_demo", planned.conversation_id or "")
    assert follow_up.entity_refs[0] in detail.messages[-1].product_refs


def test_v30_task_reference_routes_to_product_detail(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversation.db")
    conversation = repository.create_conversation("tenant_demo")
    state, product = _record_product(repository, conversation.conversation_id)
    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    checkpointer.setup()
    graph = V30ConversationGraph(repository=repository, checkpointer=checkpointer)

    response, _steps, compiled = graph.invoke(
        f"查看 {state.task_id} 关联的商品详情",
        principal=default_principal(),
        conversation_id=conversation.conversation_id,
        turn_id="turn_task_product",
    )

    assert compiled.decision.intent.value == "product_detail"
    assert response.entity_refs == [product.product_id]
