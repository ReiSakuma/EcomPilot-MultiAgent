from app.orchestration.workflow import run_workflow


def test_v1_persists_checkpoint_for_completed_task():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert state.task_id


def test_listing_and_strategy_are_both_after_market():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.nodes["listing"].dependencies == ["market", "market_price_gate"]
    assert state.nodes["strategy"].dependencies == ["market", "market_price_gate"]
    assert state.nodes["review"].dependencies == ["listing", "strategy"]
