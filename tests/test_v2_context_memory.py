from app.eval.metrics import average_context_tokens
from app.orchestration.workflow import run_workflow


def test_v2_records_context_usage_for_agents():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert "market_agent" in state.context_usage
    assert "listing_agent" in state.context_usage
    assert average_context_tokens(state) > 0


def test_v2_retrieves_merchant_memory_refs():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.memory_refs["listing_agent"]
    assert state.memory_refs["strategy_agent"]
