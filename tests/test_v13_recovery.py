from pathlib import Path

import pytest

from app.eval.recovery_eval import run_recovery_eval
from app.demo_ui import DEMO_HTML
from app.observability.store import TraceStore
from app.orchestration.checkpoint import CheckpointStore, StaleCheckpointError
from app.orchestration.recovery import RecoveryNotAllowedError, RecoveryValidationError
from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval
from app.tools.browser_tools import get_seller_center_snapshot, reset_seller_center


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def test_v13_approval_resume_runs_only_browser_node():
    reset_seller_center()
    initial = run_workflow(GOAL, approved=False)
    preserved = {
        name: initial.agent_outputs[name]
        for name in ["market_agent", "listing_agent", "strategy_agent", "review_agent"]
    }

    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(approved=True, approver="operator", reason="listing approved"),
        expected_checkpoint_version=initial.checkpoint_version,
    )
    events = TraceStore().get_run(resumed.run_id)["events"]
    started_agents = [
        event["component_name"]
        for event in events
        if event["event_type"] == "node_started"
    ]

    assert resumed.task_id == initial.task_id
    assert resumed.run_id != initial.run_id
    assert resumed.parent_run_id == initial.run_id
    assert resumed.resume_count == 1
    assert resumed.status == "completed"
    assert started_agents == ["browser_agent"]
    assert all(resumed.agent_outputs[name] == output for name, output in preserved.items())
    assert any(event["event_type"] == "checkpoint_loaded" for event in events)
    assert any(event["event_type"] == "run_resumed" for event in events)


def test_v13_browser_failure_retry_uses_idempotent_replay():
    reset_seller_center()
    failed = run_workflow(f"{GOAL}模拟执行验证失败", approved=True)

    resumed = resume_workflow(
        failed.task_id,
        retry_node="browser",
        constraint_updates={"force_execution_verification_failure": False},
        requested_by="operator",
        reason="verification mismatch corrected",
    )
    execute_records = [
        record for record in resumed.tool_records if record["tool_name"] == "browser_execute"
    ]

    assert resumed.status == "completed"
    assert len(execute_records) == 2
    assert execute_records[0]["idempotent_replay"] is False
    assert execute_records[1]["idempotent_replay"] is True
    assert len(get_seller_center_snapshot()["products"]) == 1


def test_v13_rejects_stale_checkpoint_version_without_mutation():
    state = run_workflow(GOAL, approved=False)

    with pytest.raises(StaleCheckpointError):
        resume_workflow(
            state.task_id,
            approval=Approval(approved=True, approver="operator"),
            expected_checkpoint_version=state.checkpoint_version - 1,
        )

    current = CheckpointStore().load(state.task_id)
    assert current.status == "waiting_for_approval"
    assert current.checkpoint_version == state.checkpoint_version


def test_v13_constraint_change_restarts_only_affected_branch():
    reset_seller_center()
    failed = run_workflow(GOAL.replace("成本 95", "成本 180"), approved=True)

    resumed = resume_workflow(
        failed.task_id,
        retry_node="strategy",
        constraint_updates={"cost": 95},
        requested_by="operator",
        reason="cost corrected",
    )
    events = TraceStore().get_run(resumed.run_id)["events"]
    started = {
        event["component_name"]
        for event in events
        if event["event_type"] == "node_started"
    }

    assert resumed.status == "completed"
    assert started == {
        "market_price_gate_agent",
        "listing_agent",
        "strategy_agent",
        "review_agent",
        "browser_agent",
    }
    assert resumed.nodes["market"].status.value == "completed"
    assert resumed.nodes["listing"].status.value == "completed"
    assert resumed.recovery_history[-1].action == "market_price_constraint_replan"


def test_v13_rejects_completed_task_and_invalid_constraint_patch():
    reset_seller_center()
    completed = run_workflow(GOAL, approved=True)
    with pytest.raises(RecoveryNotAllowedError):
        resume_workflow(completed.task_id, retry_node="browser")

    waiting = run_workflow(GOAL, approved=False)
    with pytest.raises(RecoveryValidationError):
        resume_workflow(
            waiting.task_id,
            approval=Approval(approved=True, approver="operator"),
            constraint_updates={"inventory": -1},
        )


def test_v13_checkpoint_metadata_reports_recoverability():
    waiting = run_workflow(GOAL, approved=False)
    metadata = CheckpointStore().get_metadata(waiting.task_id)

    assert metadata["checkpoint_version"] == waiting.checkpoint_version
    assert metadata["recoverable"] is True
    assert metadata["node_statuses"]["browser"] == "skipped"


def test_v13_recovery_eval_passes(tmp_path: Path):
    report = run_recovery_eval(
        Path("data/eval/v13_recovery_cases.json"),
        report_path=tmp_path / "recovery-report.json",
    )

    assert report["total"] == 7
    assert report["recovery_pass_rate"] == 1.0


def test_v13_ops_monitor_does_not_expose_approval_resume_control():
    assert "resumeCurrentTask" not in DEMO_HTML
    assert "审批后续跑" not in DEMO_HTML
    assert "只观察用户工作台产生的任务" in DEMO_HTML
