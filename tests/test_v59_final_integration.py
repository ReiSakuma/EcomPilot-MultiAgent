from __future__ import annotations

import json
from pathlib import Path

from app.config import PROJECT_VERSION
from app.main import app
from app.orchestration.state import TaskState
from app.release.compatibility import diagnose_checkpoint_payload
from app.release.v59 import (
    build_linkage_identity,
    build_route_evidence,
    build_v59_release_status,
    validate_live_deepseek_report,
)
from app.seller_center.schemas import ExecutionPlan
from app.seller_center.store import SellerCenterStore


def test_v60_version_keeps_v59_release_evidence_route_compatible() -> None:
    paths = {route.path for route in app.routes}

    assert PROJECT_VERSION == "0.65.0"
    assert "/api/release/v59" in paths
    assert "/api/tasks/{task_id}/linkage" in paths


def test_current_and_legacy_checkpoints_have_actionable_compatibility() -> None:
    current = TaskState(goal="current")
    current_result = diagnose_checkpoint_payload(current.model_dump(mode="json"))
    assert current_result.status == "compatible"
    assert current_result.recovery_action == "continue"

    legacy = current.model_dump(mode="json")
    legacy["schema_version"] = "1.0"
    legacy["agent_outputs"] = {
        "strategy_agent": {"price": 229, "coupon": 10, "selected_discount": 10,
                           "selected_discount_unit": "yuan"}
    }
    migrated = diagnose_checkpoint_payload(legacy)
    assert migrated.status == "migrated"
    assert migrated.recovery_action == "continue_with_migrated_view"


def test_invalid_checkpoint_preserves_conversation_with_recovery_advice() -> None:
    result = diagnose_checkpoint_payload(
        {"schema_version": "0.1", "task_id": "task_old", "goal": None}
    )

    assert result.status == "requires_regeneration"
    assert result.recovery_action == "regenerate_task_from_conversation"
    assert "原会话不会删除" in result.user_message


def test_live_deepseek_evidence_rejects_mock_estimates_and_fallbacks() -> None:
    valid = validate_live_deepseek_report(
        {
            "runs": [
                {
                    "model_records": [
                        {
                            "provider": "deepseek",
                            "status": "completed",
                            "usage_source": "actual",
                        }
                    ],
                    "model_fallbacks": [],
                }
            ]
        }
    )
    invalid = validate_live_deepseek_report(
        {
            "model_records": [
                {
                    "provider": "deterministic",
                    "status": "completed",
                    "usage_source": "estimated",
                }
            ],
            "model_fallbacks": [{"fallback": "deterministic"}],
        }
    )

    assert valid["valid"] is True
    assert invalid["valid"] is False
    assert "run_0:provider_not_deepseek" in invalid["issues"]
    assert "run_0:model_fallback_observed" in invalid["issues"]


def test_route_evidence_covers_every_v59_decision_stage() -> None:
    state = TaskState(goal="route evidence", run_id="run_v59", checkpoint_version=7)
    state.agent_outputs = {
        "market_agent": {
            "market_statistics": {"cleaned_count": 80},
            "market_layers": {"core": {"count": 50}},
            "reference_method": "median",
        },
        "market_price_gate_agent": {"status": "passed"},
        "listing_agent": {"semantic_corrections": []},
        "strategy_agent": {
            "candidate_protocol_version": "1.0",
            "candidate_proposals": [{"candidate_id": "c1"}],
            "candidate_evaluations": [{"candidate_id": "c1", "eligible": True}],
            "selected_candidate_id": "c1",
            "strategy_render_version": "1.0",
            "numeric_ownership": {"price": "tool"},
            "render_manifest": {"payload_hash": "a" * 64},
            "semantic_corrections": [{"reason": "numeric_owned"}],
        },
        "review_agent": {"correction_audit": []},
        "browser_agent": {"verification": {"verified": True}},
    }

    evidence = build_route_evidence(state)

    assert set(evidence["stages"]) == {
        "market_data_cleaning",
        "three_layer_classification",
        "market_price_gate",
        "candidate_generation",
        "tool_adjudication",
        "model_selection",
        "deterministic_render",
        "correction_audit",
        "browser_evidence",
    }
    assert all(
        item["status"] in {"observed", "not_triggered"}
        for item in evidence["stages"].values()
    )


def test_execution_provenance_links_user_trace_ops_and_seller_center() -> None:
    state = TaskState(
        goal="linkage",
        task_id="task_linked",
        run_id="run_linked",
        checkpoint_version=6,
    )
    plan = ExecutionPlan(
        operation="update_listing",
        product_id="sku_linked",
        title="无线耳机",
        price=229,
        stock=800,
        task_id=state.task_id,
        run_id=state.run_id,
        checkpoint_version=state.checkpoint_version,
    )
    store = SellerCenterStore()
    store.apply_execution_plan(plan)
    linkage = build_linkage_identity(
        state,
        trace_chain=[{"summary": {"task_id": state.task_id, "run_id": state.run_id}}],
        seller_snapshot=store.snapshot(),
    )

    assert linkage["consistent"] is True
    assert linkage["surfaces"]["seller_center"]["checkpoint_version"] == 6


def test_release_status_never_counts_external_block_as_real_validation(
    tmp_path: Path, monkeypatch
) -> None:
    from app.release import v59

    for name in ("offline", "compatibility", "run_bundles", "browser"):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"passed": True}), encoding="utf-8")
        monkeypatch.setitem(v59.REPORTS, name, path)
    live = tmp_path / "live.json"
    live.write_text(
        json.dumps({"status": "external_blocked", "error": "missing_api_key"}),
        encoding="utf-8",
    )
    monkeypatch.setitem(v59.REPORTS, "live_deepseek", live)

    status = build_v59_release_status()

    assert status.interview_ready is True
    assert status.real_external_chain_validated is False
    assert status.stages[-1].status == "external_blocked"
