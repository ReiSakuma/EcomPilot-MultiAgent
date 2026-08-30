import json
from pathlib import Path

from app.eval.badcase import classify_bad_case
from app.eval.runner import run_eval
from app.observability.recorder import TraceRecorder
from app.observability.store import TraceStore
from app.orchestration.workflow import run_workflow


HAPPY_GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def test_v11_run_has_queryable_end_to_end_trace():
    state = run_workflow(HAPPY_GOAL, approved=True)

    trace = TraceStore().get_run(state.run_id)
    event_types = {event["event_type"] for event in trace["events"]}

    assert state.run_id.startswith("run_")
    assert state.status == "completed"
    assert {
        "run_started",
        "plan_created",
        "node_started",
        "agent_completed",
        "tool_call",
        "state_transition",
        "checkpoint_saved",
        "run_completed",
    }.issubset(event_types)
    assert trace["summary"]["status"] == "completed"
    assert trace["summary"]["agent_event_count"] == 6
    assert trace["summary"]["tool_call_count"] > 0


def test_v11_trace_recorder_redacts_secrets(tmp_path: Path):
    recorder = TraceRecorder("run_redaction", trace_dir=tmp_path)
    recorder.record_event(
        "task_test",
        "run_started",
        "test",
        "fixture",
        "redaction",
        details={"api_key": "should-not-leak", "nested": {"password": "hidden"}},
    )

    payload = json.loads((tmp_path / "run_redaction.jsonl").read_text(encoding="utf-8"))
    assert payload["details"]["api_key"] == "[REDACTED]"
    assert payload["details"]["nested"]["password"] == "[REDACTED]"


def test_v11_bad_case_has_root_cause_and_recovery_advice():
    state = run_workflow(HAPPY_GOAL, approved=False)

    bad_case = classify_bad_case(state)

    assert bad_case["root_cause"] == "human_approval_missing"
    assert bad_case["expected_guardrail"] is True
    assert bad_case["recoverable"] is True
    assert bad_case["recommended_action"]


def test_v11_eval_reports_trace_coverage(tmp_path: Path):
    report = run_eval(
        Path("data/eval/v4_tasks.json"),
        report_path=tmp_path / "v11_report.json",
    )

    assert report["observability"]["trace_coverage_rate"] == 1.0
    assert report["observability"]["traced_run_count"] == 2
    assert report["observability"]["tool_call_events"] > 0
