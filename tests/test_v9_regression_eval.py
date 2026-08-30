from pathlib import Path

from app.eval.runner import run_eval
from app.orchestration.workflow import run_workflow


def test_v9_regression_dataset_passes_expectations(tmp_path: Path):
    report = run_eval(
        Path("data/eval/v9_regression_tasks.json"),
        report_path=tmp_path / "regression_report.json",
    )

    assert report["total"] == 10
    assert report["regression_pass_rate"] == 1.0
    assert report["regression_failures"] == []
    assert report["bad_case_counts"]["Constraint Violation"] >= 1
    assert report["bad_case_counts"]["Price Confirmation"] == 1
    assert report["bad_case_counts"]["Browser Execution Error"] >= 2
    assert report["bad_case_counts"].get("Retrieval Error", 0) == 0


def test_v9_margin_violation_is_classified():
    state = run_workflow(
        "我要上架一款成本 180 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "waiting_for_input"
    assert state.agent_outputs["market_price_gate_agent"]["position"] == "cost_market_conflict"
    assert "review_agent" not in state.agent_outputs


def test_v9_inventory_shortage_is_classified():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 100 件，计划首月销量 300，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "failed"
    assert "inventory_shortage" in state.agent_outputs["review_agent"]["violations"]


def test_v9_execution_verification_failure_is_caught():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。模拟执行验证失败",
        approved=True,
    )

    assert state.status == "failed"
    assert state.handoffs[-1].error == "execution_verification_failed"
