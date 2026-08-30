from __future__ import annotations

from app.config import PROJECT_VERSION
from app.eval.stability import percentile, summarize_stability_runs
from app.demo_ui import DEMO_HTML
from scripts.export_run_bundle import build_verification_matrix


def real_record(agent_name: str = "strategy_agent") -> dict:
    return {
        "agent_name": agent_name,
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "usage_source": "actual",
    }


def successful_run(*, scenario: str = "normal_listing", calls: int = 3) -> dict:
    return {
        "profile": "normal" if scenario == "normal_listing" else scenario,
        "scenario": scenario,
        "status": "completed",
        "model_records": [real_record() for _ in range(calls)],
        "context_usage": {
            "strategy_agent:stage": {
                "source_context_tokens": 1000,
                "stage_context_tokens": 400,
            }
        },
        "agent_outputs": {"strategy_agent": {}},
        "degradations": [],
    }


def test_v64_percentile_is_interpolated_and_stable() -> None:
    assert round(percentile([1, 2, 3, 4], 95) or 0, 2) == 3.85
    assert percentile([], 95) is None


def test_v64_live_summary_passes_observed_core_gates() -> None:
    runs = [successful_run() for _ in range(20)]
    report = summarize_stability_runs(runs)

    assert report["metrics"]["normal_success_rate"] == 1.0
    assert report["metrics"]["strategy_model_calls_p95"] == 3.0
    assert report["metrics"]["strategy_context_reduction_p95"] == 0.6
    assert report["gates"]["real_deepseek_records_observed"]["status"] == "pass"
    assert report["gates"]["truncation_recovery_100_percent"]["status"] == "not_observed"


def test_v64_summary_detects_strategy_call_budget_regression() -> None:
    runs = [successful_run(calls=5) for _ in range(20)]
    report = summarize_stability_runs(runs)

    assert report["gates"]["strategy_model_calls_p95_at_most_4"]["status"] == "fail"


def test_v64_summary_records_recovered_truncation() -> None:
    run = successful_run(scenario="selection_truncation")
    run["model_fallbacks"] = [{"reason": "selection output truncated"}]
    report = summarize_stability_runs([run])

    assert report["metrics"]["truncation_case_count"] == 1
    assert report["metrics"]["truncation_recovery_rate"] == 1.0
    assert report["gates"]["truncation_recovery_100_percent"]["status"] == "pass"


def test_v64_release_identity_and_ops_stability_panel() -> None:
    assert PROJECT_VERSION == "0.65.0"
    assert "v65 精简核心" in DEMO_HTML
    assert "strategy_protocol" in DEMO_HTML
    assert "strategy_context_reduction" in DEMO_HTML


def test_v64_run_bundle_exposes_final_stability_evidence() -> None:
    state = {
        "status": "completed",
        "approved": True,
        "model_records": [],
        "tool_records": [],
        "nodes": {"browser": {"status": "completed"}},
        "context_usage": {
            "strategy_agent:stage": {
                "source_context_tokens": 1000,
                "stage_context_tokens": 400,
            }
        },
        "agent_outputs": {
            "strategy_agent": {
                "evidence_plan": {"selected_tools": []},
                "evidence_ledger": {"plan_status": "completed"},
                "candidate_budget": {
                    "logical_model_calls": {"hard_limit": 4, "calls_used": 3}
                },
                "candidate_evaluations": [
                    {"candidate_id": "candidate_a", "eligible": True}
                ],
                "selected_candidate_id": "candidate_a",
                "candidate_selection_mode": "model_selected",
            },
            "review_agent": {"violations": []},
            "browser_agent": {"verification": {"verified": True}},
        },
        "a2a_delegations": {},
        "degradations": [
            {
                "code": "compact_fallback",
                "stage": "strategy",
                "developer_message": "selection output was repaired",
                "trace_refs": ["run_1:event_9"],
            }
        ],
    }
    matrix = build_verification_matrix(state, [], {"summary": {}})

    assert matrix["strategy_stage_context_projection"]["status"] == "pass"
    assert matrix["strategy_logical_model_call_budget"]["status"] == "pass"
    assert matrix["strategy_candidate_finalization"]["status"] == "pass"
    assert matrix["degradation_traceability"]["status"] == "pass"
