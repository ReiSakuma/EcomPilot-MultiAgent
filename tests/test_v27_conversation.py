from __future__ import annotations

from app.access.models import default_principal
from app.copilot.facade import ConversationFacade
from app.copilot.graph import V27ConversationGraph
from app.copilot.parity import run_graph_parity
from app.copilot.schemas import CopilotResponse
from app.copilot_ui import COPILOT_HTML
from app.orchestration.failures import TaskOutcome
from app.orchestration.workflow import run_workflow


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，主要面向游戏爱好者，"
    "库存800件，毛利率不能低于40%。已确认的产品功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认的产品形态：未确认。运营目标：主打性价比。"
)


def test_v27_copilot_response_projects_legacy_workflow() -> None:
    state = run_workflow(GOAL, approved=False)
    response = ConversationFacade.build_response(state)
    payload = response.model_dump(mode="json")
    panels = {panel.panel_id: panel for panel in response.panels}

    assert response.protocol_version == "1.7"
    assert response.outcome.value == "awaiting_approval"
    assert response.approval_required is True
    assert response.store_modified is False
    assert response.understood_requirements["target_price"] == 199
    assert panels["strategy"].data["margin"]["margin_rate"] >= 0.40
    assert panels["review"].data["approved_for_execution"] is True
    assert panels["execution"].status == "waiting"
    assert response.action_summary.trace_event_count > 0
    assert any(step.artifact_refs for step in response.action_summary.steps)
    assert any(step.trace_refs for step in response.action_summary.steps)
    assert payload["model_usage"]["actual_call_count"] == 0
    assert "agent_outputs" not in payload
    assert "model_records" not in payload


def test_v27_model_usage_separates_real_calls_test_stubs_and_no_call() -> None:
    state = run_workflow(GOAL, approved=False)
    no_call = ConversationFacade.build_response(state).model_usage
    assert no_call.mode == "no_model_call"

    state.model_records.append(
        {"provider": "deterministic", "model": "fixture", "status": "completed"}
    )
    stub = ConversationFacade.build_response(state).model_usage
    assert stub.mode == "test_stub"
    assert stub.stub_call_count == 1
    assert stub.actual_call_count == 0

    state.model_records.append(
        {"provider": "deepseek", "model": "deepseek-v4-pro", "status": "completed"}
    )
    real = ConversationFacade.build_response(state).model_usage
    assert real.mode == "real_model"
    assert real.actual_call_count == 1
    assert real.recorded_call_count == 2


def test_needs_attention_is_a_structured_public_technical_result() -> None:
    state = run_workflow(GOAL, approved=False)
    state.outcome = TaskOutcome.needs_attention
    state.status = "needs_attention"

    response = ConversationFacade.build_response(state)

    assert response.outcome.value == "technical_failed"
    assert response.store_modified is False
    assert "需要核对" in response.assistant_message


def test_v27_graph_wraps_the_legacy_workflow_without_replacing_it() -> None:
    response, steps = V27ConversationGraph().invoke(
        GOAL, principal=default_principal()
    )

    assert isinstance(response, CopilotResponse)
    assert steps == ["receive", "legacy_listing_workflow", "answer"]
    assert response.outcome.value == "awaiting_approval"


def test_v27_graph_parity_matches_business_projection() -> None:
    report = run_graph_parity(GOAL, principal=default_principal())

    assert report.passed is True
    assert report.differences == []
    assert report.legacy_projection == report.graph_projection


def test_v27_facade_approval_executes_and_verifies_mock_store() -> None:
    facade = ConversationFacade()
    first = facade.handle_message(GOAL, principal=default_principal())
    completed = facade.approve(
        first.task_id,
        principal=default_principal(),
        expected_checkpoint_version=None,
    )
    execution = next(
        panel for panel in completed.panels if panel.panel_id == "execution"
    )

    assert completed.outcome.value == "completed"
    assert completed.store_modified is True
    assert completed.approval_required is False
    assert execution.status == "completed"
    assert execution.data["verification"]["verified"] is True


def test_v27_user_surface_is_conversational_and_keeps_business_panels() -> None:
    assert "对话式电商运营工作台" in COPILOT_HTML
    assert 'class="history"' in COPILOT_HTML
    assert 'class="conversation"' in COPILOT_HTML
    assert 'class="workspace"' in COPILOT_HTML
    assert "系统理解的需求" in COPILOT_HTML
    assert "商品页面方案" in COPILOT_HTML
    assert "定价与促销" in COPILOT_HTML
    assert "风险与修改建议" in COPILOT_HTML
    assert "/api/copilot/messages" in COPILOT_HTML
    assert "/api/copilot/conversations" in COPILOT_HTML
    assert "client_request_id" in COPILOT_HTML
    assert "loadConversations" in COPILOT_HTML
    assert "openConversation" in COPILOT_HTML
    assert "/api/copilot/tasks/" in COPILOT_HTML
    assert "本次实际模型调用" in COPILOT_HTML
    assert "readJsonResponse" in COPILOT_HTML
    assert "服务暂时无法完成请求" in COPILOT_HTML
    assert "test_stub" in COPILOT_HTML
    assert "Agent 节点" not in COPILOT_HTML
    assert "Raw JSON" not in COPILOT_HTML


def test_v27_api_contract_runs_and_approves_same_task(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "require_linked_runtime", lambda: None)
    first = main_module.copilot_message(
        main_module.CopilotMessageRequest(message=GOAL)
    )

    assert first.protocol_version == "1.7"
    assert first.outcome.value == "awaiting_approval"

    completed = main_module.approve_copilot_task(
        first.task_id,
        main_module.CopilotApprovalRequest(reason="v27 API contract"),
    )
    assert completed.task_id == first.task_id
    assert completed.outcome.value == "completed"
    assert completed.store_modified is True
