from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.sqlite import SqliteSaver

from app.access.models import default_principal
from app.agents.supervisor import Supervisor
from app.conversations.repository import ConversationRepository
from app.copilot.compiler import RequestCompiler
from app.copilot.graph import V32ConversationGraph
from app.copilot.routing import ConversationOrchestrator
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.workflow import run_workflow
from app.products.ledger import ProductLedger
from app.safety.approval import Approval
from app.seller_center.store import SELLER_CENTER_STORE


def _route(message: str):
    compiled = RequestCompiler().compile(message)
    compiled.route_plan = ConversationOrchestrator().plan(compiled)
    return compiled.route_plan


def main() -> None:
    SELLER_CENTER_STORE.reset()
    with tempfile.TemporaryDirectory(prefix="ecompilot_v32_") as directory:
        root = Path(directory)
        repository = ConversationRepository(root / "conversation.db")
        conversation = repository.create_conversation("tenant_demo", title="v32 acceptance")
        original = run_workflow(
            "我要上架一款成本95元的无线耳机，售价300元，库存800件，"
            "毛利率不低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟。",
            approved=True,
        )
        original.conversation_id = conversation.conversation_id
        original.turn_id = "turn_create"
        ledger = ProductLedger(root / "conversation.db")
        product = ledger.record_successful_execution(original)
        before = dict(
            SELLER_CENTER_STORE.snapshot(tenant_id="tenant_demo")["products"][product.product_id]
        )

        saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
        saver.setup()
        graph = V32ConversationGraph(repository=repository, checkpointer=saver)
        response, _steps, _compiled = graph.invoke(
            "把上次那个耳机价格改为320元，库存改为700件",
            principal=default_principal(),
            conversation_id=conversation.conversation_id,
            turn_id="turn_modify",
        )
        waiting = CheckpointStore().load(response.task_id)
        unchanged_before_approval = (
            SELLER_CENTER_STORE.snapshot(tenant_id="tenant_demo")["products"][product.product_id]
            == before
        )
        unapproved_write_calls = sum(
            record.get("tool_name") == "browser_execute"
            and record.get("status") == "completed"
            for record in waiting.tool_records
        )
        completed = Supervisor().resume(
            waiting.task_id,
            approval=Approval(approved=True, approver="acceptance_user", reason="确认变更"),
        )
        after = SELLER_CENTER_STORE.snapshot(tenant_id="tenant_demo")["products"][product.product_id]
        ledger.record_successful_execution(completed)
        modified_link = ledger.detail("tenant_demo", product.product_id).task_links[-1]

        routes = {
            "listing": _route("我要上架一款成本95元的无线耳机，售价300元，库存800件"),
            "market": _route("我想了解无线耳机的整体价格区间"),
            "analytics": _route("上次那个耳机最近30天销售额怎么样"),
            "modify": _route("把上次那个耳机价格改为320元"),
            "chat": _route("清华和哈工大哪个更好"),
        }
        checks = {
            "five_templates_selected": len({route.template_id for route in routes.values()}) == 5,
            "market_only_market_agent": routes["market"].planned_agents == ["market_agent"],
            "analytics_only_analytics_agent": routes["analytics"].planned_agents == ["analytics_agent"],
            "chat_has_no_specialist": routes["chat"].planned_agents == [],
            "modify_requires_approval": response.approval_required,
            "unapproved_write_call_count_zero": unapproved_write_calls == 0,
            "store_unchanged_before_approval": unchanged_before_approval,
            "approved_price_exact": after["price"] == 320,
            "approved_stock_exact": after["stock"] == 700,
            "unrequested_title_preserved": after["title"] == before["title"],
            "unrequested_bullets_preserved": after["bullets"] == before["bullets"],
            "same_product_identity": completed.entity_refs == [product.product_id],
            "ledger_relation_modified": modified_link.relation == "modified",
            "route_plan_persisted": waiting.route_plan.get("template_id") == "modify_listing_workflow.v1",
        }
        result = {
            "version": "v32",
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "route_templates": {name: route.template_id for name, route in routes.items()},
            "modified_product_id": product.product_id,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "passed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
