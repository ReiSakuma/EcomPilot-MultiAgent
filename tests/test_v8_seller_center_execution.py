from app.eval.metrics import execution_verification_rate
from app.orchestration.workflow import run_workflow
from app.seller_center.schemas import ExecutionPlan
from app.tools.browser_tools import (
    browser_execute,
    browser_verify,
    get_seller_center_snapshot,
    reset_seller_center,
)


def test_v8_browser_execute_writes_and_verifies_seller_center():
    reset_seller_center()
    plan = {
        "operation": "update_listing",
        "product_id": "p1",
        "title": "测试无线耳机",
        "bullets": ["低延迟", "长续航"],
        "price": 199,
        "stock": 800,
        "coupon": 20,
    }

    result = browser_execute(plan, idempotency_key="test:p1:update")
    verification = browser_verify(plan)
    snapshot = get_seller_center_snapshot()

    assert result["status"] == "applied"
    assert verification["verified"] is True
    assert snapshot["products"]["p1"]["title"] == "测试无线耳机"
    assert snapshot["promotions"]["coupon_p1"]["coupon"] == 20


def test_v8_workflow_browser_verification_rate():
    reset_seller_center()
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert execution_verification_rate(state) == 1.0
    assert state.agent_outputs["browser_agent"]["verification"]["verified"] is True


def test_v8_unapproved_workflow_does_not_write_seller_center():
    reset_seller_center()
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=False,
    )

    assert state.status == "waiting_for_approval"
    assert get_seller_center_snapshot()["products"] == {}


def test_v8_execution_plan_schema_rejects_unsupported_operation():
    try:
        ExecutionPlan.model_validate({"operation": "delete_product", "product_id": "p1"})
    except Exception:
        return
    raise AssertionError("Expected validation error")
