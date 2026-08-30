from app.eval.metrics import constraint_satisfaction, task_success
from app.orchestration.workflow import run_workflow


def test_v0_workflow_completes_with_approval():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert task_success(state)
    assert constraint_satisfaction(state)
    assert state.agent_outputs["strategy_agent"]["margin"]["margin_rate"] >= 0.25


def test_high_risk_write_waits_without_approval():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=False,
    )

    assert state.status == "waiting_for_approval"
    assert state.handoffs[-1].error == "human_approval_required"
