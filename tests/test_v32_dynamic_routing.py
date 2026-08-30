from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.access.models import default_principal
from app.agents.supervisor import Supervisor
from app.seller_center.store import SELLER_CENTER_STORE
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.graph import V32ConversationGraph
from app.copilot.routing import ConversationOrchestrator
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.workflow import run_workflow
from app.products.ledger import ProductLedger
from app.safety.approval import Approval
from app.orchestration.a2a import A2ADelegationRequest
from app.security.capability_tokens import CapabilityAuthority, CapabilityAuthorizationError
import pytest


def _compiled(message: str):
    request = RequestCompiler().compile(message)
    request.route_plan = ConversationOrchestrator().plan(request)
    return request


def test_v32_route_registry_selects_minimal_read_only_agents() -> None:
    market = _compiled("我想了解无线耳机的整体价格区间和评论情况")
    analytics = _compiled("上次那个耳机最近30天销售额和库存趋势怎么样")
    chat = _compiled("清华和哈工大哪个更好")

    assert market.route_plan.template_id == "market_read_only.v1"
    assert market.route_plan.planned_agents == ["market_agent"]
    assert "browser_agent" in market.route_plan.skipped_agents
    assert analytics.route_plan.planned_agents == ["analytics_agent"]
    assert analytics.route_plan.risk_scope == "read"
    assert chat.route_plan.planned_agents == []


def test_v32_modify_compiler_creates_explicit_field_change_plan() -> None:
    compiled = _compiled("把上次那个耳机价格改为320元，库存调整为700件")

    assert compiled.decision.intent.value == "modify_listing"
    assert compiled.assessment.approval_required is True
    assert compiled.route_plan.template_id == "modify_listing_workflow.v1"
    assert compiled.structured_request["changes"] == [
        {"field": "target_price", "new_value": 320.0, "source": "user_explicit"},
        {"field": "inventory", "new_value": 700, "source": "user_explicit"},
    ]


def test_v32_write_execute_token_is_approval_bound() -> None:
    now = datetime.now(timezone.utc)
    base = dict(
        task_id="task_token",
        delegation_id="dlg_token",
        tenant_id="tenant_demo",
        conversation_id="conv_token",
        turn_id="turn_token",
        intent="modify_listing",
        sender_agent="supervisor",
        receiver_agent="browser_agent",
        capability_id="seller.execute",
        instruction="execute approved field changes",
        input_state_version=3,
        idempotency_key="token-test",
        created_at=now,
        deadline_at=now + timedelta(minutes=1),
        risk_scope="write_execute",
        capability_access="write_execute",
    )
    authority = CapabilityAuthority(secret=b"v32-test-secret-material-32-bytes!!")
    denied_request = A2ADelegationRequest(**base, approval_granted=False)
    denied = authority.issue(denied_request, allowed_tools=("browser_execute",), max_uses=1)
    with pytest.raises(CapabilityAuthorizationError, match="requires task-bound user approval"):
        authority.verify_and_consume(
            denied.token,
            task_id="task_token",
            delegation_id="dlg_token",
            capability_id="seller.execute",
            agent_name="browser_agent",
            tool_name="browser_execute",
            tenant_id="tenant_demo",
        )


def test_v32_a2a_delegations_carry_conversation_intent_and_access_tier() -> None:
    state = Supervisor().run(
        "我要上架一款成本95元的无线耳机，售价199元，库存800件，毛利率不低于40%。",
        approved=False,
        conversation_id="conv_route",
        turn_id="turn_route",
    )

    tiers = {
        record.request.receiver_agent: record.request.capability_access
        for record in state.a2a_delegations.values()
    }
    assert tiers["market_agent"] == "read"
    assert tiers["listing_agent"] == "write_plan"
    assert tiers["strategy_agent"] == "write_plan"
    assert tiers["review_agent"] == "write_plan"
    assert tiers["browser_agent"] == "write_execute"
    browser_delegation = next(
        record for record in state.a2a_delegations.values()
        if record.request.receiver_agent == "browser_agent"
    )
    assert browser_delegation.request.approval_granted is False
    assert all(record["tool_name"] != "browser_execute" for record in state.tool_records)
    assert all(
        record.request.conversation_id == "conv_route"
        and record.request.turn_id == "turn_route"
        and record.request.intent == "create_listing"
        for record in state.a2a_delegations.values()
    )


def test_v32_modify_existing_product_requires_approval_and_changes_only_requested_fields(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "conversation.db"
    repository = ConversationRepository(database_path)
    conversation = repository.create_conversation("tenant_demo", title="耳机运营")
    original = run_workflow(
        "我要上架一款成本95元的无线耳机，售价199元，库存800件，"
        "毛利率不低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟。",
        approved=True,
    )
    original.conversation_id = conversation.conversation_id
    original.turn_id = "turn_create"
    ledger = ProductLedger(database_path)
    product = ledger.record_successful_execution(original)
    before = SELLER_CENTER_STORE.snapshot(tenant_id="tenant_demo")["products"][product.product_id].copy()

    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    checkpointer.setup()
    graph = V32ConversationGraph(repository=repository, checkpointer=checkpointer)
    response, steps, compiled = graph.invoke(
        "把上次那个耳机价格改为209元，库存改为700件",
        principal=default_principal(),
        conversation_id=conversation.conversation_id,
        turn_id="turn_modify",
    )

    assert response.approval_required is True
    assert response.store_modified is False
    assert steps == ["receive", "compile_request", "preflight_gate", "modify_listing_workflow", "answer"]
    assert SELLER_CENTER_STORE.snapshot(tenant_id="tenant_demo")["products"][product.product_id] == before
    state = CheckpointStore().load(response.task_id)
    assert state.route_plan["template_id"] == "modify_listing_workflow.v1"
    assert state.agent_outputs["review_agent"]["execution_plan"]["product_id"] == product.product_id

    completed = Supervisor().resume(
        state.task_id,
        approval=Approval(approved=True, approver="user_demo", reason="确认字段变更"),
    )
    after = SELLER_CENTER_STORE.snapshot(tenant_id="tenant_demo")["products"][product.product_id]
    assert completed.agent_outputs["browser_agent"]["verification"]["verified"] is True
    assert after["price"] == 209
    assert after["stock"] == 700
    assert after["title"] == before["title"]
    assert after["bullets"] == before["bullets"]

    ledger.record_successful_execution(completed)
    detail = ledger.detail("tenant_demo", product.product_id)
    assert detail.task_links[-1].relation == "modified"
