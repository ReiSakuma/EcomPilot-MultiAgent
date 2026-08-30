from __future__ import annotations

import pytest

from app.access.models import default_principal
from app.agents.supervisor import Supervisor
from app.conversations.repository import ConversationRepository
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.orchestration.checkpoint import CheckpointStore
from app.orchestration.checkpoint import StaleCheckpointError
from app.orchestration.recovery import RecoveryValidationError
from app.tools.market_price_gate import (
    MarketPriceAssessmentInput,
    assess_market_price_position,
)


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 300 元，库存 800 件，"
    "主要面向游戏爱好者，毛利率不能低于 40%。"
    "已确认的产品功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
    "已确认的产品形态：未确认。"
)


def _assessment(**overrides):
    payload = {
        "target_price": 199,
        "cost": 95,
        "min_margin_rate": 0.4,
        "category": "无线耳机",
        "pricing_profile": "standard",
        "core_reference_price": 189,
        "reference_method": "core_cleaned_mean",
        "core_mean_price": 189,
        "core_median_price": 189,
        "core_price_band": (129, 239),
        "full_market_band": (49, 899),
        "core_sample_count": 93,
        "excluded_sample_count": 2,
        "market_mode": "decision_ready",
        "distribution_status": "stable",
    }
    payload.update(overrides)
    return assess_market_price_position(MarketPriceAssessmentInput(**payload))


def test_v54_pure_gate_covers_normal_high_low_and_cost_conflict() -> None:
    normal = _assessment()
    high = _assessment(target_price=300)
    low = _assessment(target_price=129)
    conflict = _assessment(target_price=300, cost=200)

    assert normal.status == "passed"
    assert normal.position == "within_market"
    assert high.status == "confirmation_required"
    assert high.position == "above_market"
    assert high.suggested_price_range == (169.0, 209.0)
    assert low.status == "confirmation_required"
    assert low.position == "below_market"
    assert conflict.status == "confirmation_required"
    assert conflict.position == "cost_market_conflict"
    assert conflict.suggested_price_range is None


def test_v54_low_quality_evidence_is_advisory_not_a_hard_gate() -> None:
    result = _assessment(
        target_price=300,
        core_sample_count=3,
        market_mode="advisory_only",
        distribution_status="insufficient",
    )

    assert result.status == "advisory_only"
    assert result.position == "above_market"
    assert "market_evidence_not_strong_enough_for_hard_gate" in result.warnings


def test_v54_override_requires_and_records_user_evidence() -> None:
    with pytest.raises(ValueError, match="requires user-confirmed evidence"):
        _assessment(target_price=300, pricing_override=True)

    result = _assessment(
        target_price=300,
        pricing_override=True,
        pricing_override_evidence=("已核验的独家芯片和两年质保定位",),
    )
    assert result.status == "passed"
    assert result.override_applied is True
    assert result.override_evidence == ("已核验的独家芯片和两年质保定位",)


def test_v54_workflow_stops_before_listing_and_reuses_market_after_adoption() -> None:
    waiting = Supervisor().run(
        GOAL,
        principal=default_principal(),
        conversation_id="conv_v54_adopt",
        turn_id="turn_v54_adopt_1",
    )
    original_version = waiting.checkpoint_version
    market = waiting.agent_outputs["market_agent"]
    market_calls = sum(
        record.get("tool_name") == "build_market_report"
        for record in waiting.tool_records
    )

    assert waiting.status == "waiting_for_input"
    assert waiting.nodes["market_price_gate"].status.value == "completed"
    assert waiting.nodes["listing"].status.value == "pending"
    assert waiting.nodes["strategy"].status.value == "pending"
    assert set(waiting.agent_outputs) == {"market_agent", "market_price_gate_agent"}

    resumed = Supervisor().resume(
        waiting.task_id,
        constraint_updates={
            "pricing_confirmation_action": "adopt_suggested_price",
            "target_price": 189,
            "price_confirmation_request_id": "confirm_v54_adopt",
        },
        expected_checkpoint_version=original_version,
        requested_by="demo-merchant-a",
        turn_id="turn_v54_adopt_2",
    )

    assert resumed.status == "waiting_for_approval"
    assert resumed.constraints["target_price"] == 189
    assert resumed.agent_outputs["market_agent"] == market
    assert sum(
        record.get("tool_name") == "build_market_report"
        for record in resumed.tool_records
    ) == market_calls == 1
    assert resumed.agent_outputs["market_price_gate_agent"]["status"] == "passed"
    assert resumed.agent_outputs["strategy_agent"]["price"] == 189

    with pytest.raises(StaleCheckpointError):
        Supervisor().resume(
            waiting.task_id,
            constraint_updates={
                "pricing_confirmation_action": "adopt_suggested_price",
                "target_price": 189,
                "price_confirmation_request_id": "confirm_v54_adopt",
            },
            expected_checkpoint_version=original_version,
        )


def test_v54_keep_original_requires_evidence_and_market_only_finishes_read_only() -> None:
    no_evidence = Supervisor().run(
        GOAL,
        principal=default_principal(),
        conversation_id="conv_v54_override",
        turn_id="turn_v54_override_1",
    )
    with pytest.raises(RecoveryValidationError, match="requires pricing_override_evidence"):
        Supervisor().resume(
            no_evidence.task_id,
            constraint_updates={
                "pricing_confirmation_action": "keep_original_with_evidence",
                "price_confirmation_request_id": "confirm_v54_no_evidence",
            },
            expected_checkpoint_version=no_evidence.checkpoint_version,
        )

    overridden = Supervisor().resume(
        no_evidence.task_id,
        constraint_updates={
            "pricing_confirmation_action": "keep_original_with_evidence",
            "pricing_override_evidence": ["已核验的独家芯片和两年质保定位"],
            "price_confirmation_request_id": "confirm_v54_override",
        },
        expected_checkpoint_version=no_evidence.checkpoint_version,
    )
    assert overridden.status == "waiting_for_approval"
    gate = overridden.agent_outputs["market_price_gate_agent"]
    assert gate["override_applied"] is True
    assert gate["override_evidence"] == ["已核验的独家芯片和两年质保定位"]

    analysis_only = Supervisor().run(
        GOAL,
        principal=default_principal(),
        conversation_id="conv_v54_read_only",
        turn_id="turn_v54_read_only_1",
    )
    finished = Supervisor().resume(
        analysis_only.task_id,
        constraint_updates={
            "pricing_confirmation_action": "market_analysis_only",
            "price_confirmation_request_id": "confirm_v54_read_only",
        },
        expected_checkpoint_version=analysis_only.checkpoint_version,
    )
    assert finished.status == "completed"
    assert all(
        finished.nodes[node_id].status.value == "skipped"
        for node_id in ("listing", "strategy", "review", "browser")
    )
    assert "browser_agent" not in finished.agent_outputs


def test_v54_conversation_confirmation_resumes_same_task_idempotently(tmp_path) -> None:
    facade = ConversationFacade(
        repository=ConversationRepository(tmp_path / "conversations.db")
    )
    principal = default_principal()
    first = facade.handle_message(
        GOAL,
        principal=principal,
        client_request_id="request_v54_price_gate",
    )

    assert first.outcome is CopilotOutcome.waiting_for_input
    pending = facade.repository.get_pending_request(
        principal.tenant_id, first.conversation_id
    )
    assert pending is not None
    assert pending.compiled_payload["_market_price_confirmation"]["task_id"] == first.task_id

    resumed = facade.handle_message(
        "采用建议价格",
        principal=principal,
        conversation_id=first.conversation_id,
        client_request_id="request_v54_price_confirm",
    )
    replay = facade.handle_message(
        "采用建议价格",
        principal=principal,
        conversation_id=first.conversation_id,
        client_request_id="request_v54_price_confirm",
    )

    assert resumed.outcome is CopilotOutcome.awaiting_approval
    assert resumed.task_id == first.task_id
    assert replay.response_id == resumed.response_id
    state = CheckpointStore().load(first.task_id)
    assert sum(
        record.get("tool_name") == "build_market_report"
        for record in state.tool_records
    ) == 1
