from __future__ import annotations

from uuid import uuid4

import pytest

from app import main as api
from app.access.models import default_principal
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.copilot_ui import COPILOT_HTML
from app.demo_ui import DEMO_HTML
from app.main import PriceConfirmationRequest
from app.orchestration.checkpoint import CheckpointStore


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 300 元，库存 800 件，"
    "主要面向游戏爱好者，毛利率不能低于 40%。"
    "已确认的产品功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
    "已确认的产品形态：未确认。"
)

NORMAL_PRICE_GOAL = (
    "我要上架一款成本95元的入耳式无线耳机，目标售价199元，库存800件，"
    "主要面向游戏爱好者，毛利率不能低于25%。"
    "已确认的产品功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
    "已确认的产品形态：入耳式。"
)


def _waiting_response():
    token = uuid4().hex
    response = ConversationFacade().handle_message(
        GOAL,
        principal=default_principal(),
        client_request_id=f"v55_start_{token}",
    )
    assert response.outcome is CopilotOutcome.waiting_for_input
    assert response.price_confirmation is not None
    return response, token


def test_v55_price_inside_acceptance_band_does_not_request_confirmation() -> None:
    token = uuid4().hex
    response = ConversationFacade().handle_message(
        NORMAL_PRICE_GOAL,
        principal=default_principal(),
        client_request_id=f"v55_normal_price_{token}",
    )

    assert response.price_confirmation is None
    assert "再次确认" not in response.assistant_message
    if response.task_id:
        state = CheckpointStore().load(response.task_id)
        gate = state.agent_outputs["market_price_gate_agent"]
        assert gate["status"] == "passed"
        assert gate["position"] == "within_market"


def test_v55_waiting_response_exposes_three_layer_evidence_and_progress() -> None:
    response, _ = _waiting_response()
    prompt = response.price_confirmation
    assert prompt is not None

    assert prompt.core_reference_price is not None
    assert prompt.core_price_band is not None
    assert prompt.adjacent_price_band is not None
    assert prompt.full_market_band is not None
    assert prompt.core_sample_count > 0
    assert prompt.full_market_sample_count >= prompt.core_sample_count
    assert prompt.excluded_sample_count == 2
    assert {option.action for option in prompt.options} == {
        "adopt_suggested_price",
        "keep_original_with_evidence",
        "market_analysis_only",
    }

    step_ids = [step.step_id for step in response.action_summary.steps]
    assert step_ids.index("market_data_cleaning") < step_ids.index("market")
    assert step_ids.index("market") < step_ids.index("market_price_gate")
    assert next(
        step for step in response.action_summary.steps if step.step_id == "listing"
    ).status == "pending"


@pytest.mark.parametrize(
    ("action", "evidence", "expected_outcome"),
    [
        ("adopt_suggested_price", None, CopilotOutcome.awaiting_approval),
        (
            "keep_original_with_evidence",
            "已核验的独家游戏芯片与两年质保定位",
            CopilotOutcome.awaiting_approval,
        ),
        ("market_analysis_only", None, CopilotOutcome.read_only_completed),
    ],
)
def test_v55_confirmation_api_resumes_same_task_and_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    evidence: str | None,
    expected_outcome: CopilotOutcome,
) -> None:
    waiting, token = _waiting_response()
    prompt = waiting.price_confirmation
    assert prompt is not None
    before = CheckpointStore().load(waiting.task_id)
    market_hash = before.agent_outputs["market_agent"]["market_layers"]["content_hash"]
    market_calls = sum(
        record.get("tool_name") == "build_market_report"
        for record in before.tool_records
    )
    option = next(item for item in prompt.options if item.action == action)
    monkeypatch.setattr(api, "require_linked_runtime", lambda: None)

    result = api.confirm_market_price(
        waiting.task_id,
        PriceConfirmationRequest(
            action=action,
            selected_price=option.suggested_price,
            evidence=evidence,
            expected_checkpoint_version=prompt.checkpoint_version,
            client_request_id=f"v55_confirm_{action}_{token}",
        ),
    )

    assert result.task_id == waiting.task_id
    assert result.conversation_id == waiting.conversation_id
    assert result.outcome is expected_outcome
    resumed = CheckpointStore().load(waiting.task_id)
    assert resumed.checkpoint_version > prompt.checkpoint_version
    assert resumed.parent_run_id == before.run_id
    assert resumed.agent_outputs["market_agent"]["market_layers"]["content_hash"] == market_hash
    assert sum(
        record.get("tool_name") == "build_market_report"
        for record in resumed.tool_records
    ) == market_calls == 1
    if action == "market_analysis_only":
        assert "listing_agent" not in resumed.agent_outputs
        assert "strategy_agent" not in resumed.agent_outputs
    else:
        assert resumed.nodes["listing"].status.value == "completed"
        assert resumed.nodes["strategy"].status.value == "completed"


def test_v55_user_and_operations_surfaces_have_separate_responsibilities() -> None:
    assert 'id="priceConfirmation"' in COPILOT_HTML
    assert 'id="adoptPriceButton"' in COPILOT_HTML
    assert 'id="keepPriceButton"' in COPILOT_HTML
    assert 'id="marketOnlyButton"' in COPILOT_HTML
    assert "核心可比商品" in COPILOT_HTML
    assert "相邻档次商品" in COPILOT_HTML
    assert ".decision.technical_failed" in COPILOT_HTML
    assert ".decision.business_rejected,.decision.waiting_for_input" in COPILOT_HTML

    assert "运维监控台（只读）" in DEMO_HTML
    assert "核心可比层（用于价格决策）" in DEMO_HTML
    assert "相邻档次层（只作解释）" in DEMO_HTML
    assert "被排除的脏样本" in DEMO_HTML
    assert "保留的极端但可解释样本" in DEMO_HTML
    assert "checkpoint_version" in DEMO_HTML
    assert "/price-confirmation" not in DEMO_HTML
    assert "/approve" not in DEMO_HTML
    assert "resumeCurrentTask" not in DEMO_HTML
