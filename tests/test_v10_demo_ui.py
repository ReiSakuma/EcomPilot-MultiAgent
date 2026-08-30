from pathlib import Path

from app.demo_ui import DEMO_HTML
from fastapi import HTTPException

from app import linked_runtime
from app.main import (
    demo_page,
    ops_page,
    require_linked_runtime,
    root_page,
    seller_center_page,
    user_page,
)
from app.trace_ui import TRACE_HTML
from app.user_ui import USER_HTML
from app.eval.runner import run_eval
from app.orchestration.workflow import run_workflow


def test_v10_demo_html_is_read_only_operations_monitor():
    assert "EcomPilot 运维监控台（只读）" in DEMO_HTML
    assert "该页面只观察用户工作台产生的任务" in DEMO_HTML
    assert "seller-center/state" in DEMO_HTML
    assert "用户工作台" in DEMO_HTML
    assert "审批并执行" not in DEMO_HTML
    assert "不审批运行" not in DEMO_HTML
    assert "审批后续跑" not in DEMO_HTML
    assert "runTask" not in DEMO_HTML
    assert "resumeCurrentTask" not in DEMO_HTML
    assert "method: 'POST'" not in DEMO_HTML
    assert "/tasks/run" not in DEMO_HTML
    assert "/seller-center/reset" not in DEMO_HTML


def test_user_workspace_hides_agent_internals_and_keeps_product_flow():
    assert "EcomPilot" in USER_HTML
    assert "对话式电商运营工作台" in USER_HTML
    assert "发送" in USER_HTML
    assert "确认并同步到模拟店铺" in USER_HTML
    assert "商品页面方案" in USER_HTML
    assert "风险与修改建议" in USER_HTML
    assert "Agent 节点" not in USER_HTML
    assert "Raw JSON" not in USER_HTML
    assert "/api/copilot/messages" in USER_HTML
    assert "/linked/status" in USER_HTML
    assert "真实模型未连接" in USER_HTML


def test_user_workspace_separates_technical_failure_from_business_rejection():
    assert "系统执行遇到技术问题" in USER_HTML
    assert "当前条件需要调整" in USER_HTML
    assert "response.failure" in USER_HTML
    assert "本次没有修改模拟店铺" in USER_HTML


def test_ui_routes_separate_user_workspace_and_operations_console():
    assert root_page() == USER_HTML
    assert demo_page() == USER_HTML
    assert user_page() == USER_HTML
    assert ops_page() == DEMO_HTML


def test_linked_pages_poll_the_same_task_run_and_store():
    assert "setInterval(loadLinkedTask, 1500)" in DEMO_HTML
    assert "requestedTaskId" in DEMO_HTML
    assert "const pinnedTask = pageParams.get('pin') === '1'" in DEMO_HTML
    assert "if (!pinnedTask)" in DEMO_HTML
    assert "setInterval(refreshLinkedRun, 1500)" in TRACE_HTML
    assert "requestedRunId" in TRACE_HTML
    assert "let followLatest = pageParams.get('pin') !== '1'" in TRACE_HTML
    assert "setInterval(loadState, 1500)" in seller_center_page()


def test_linked_runtime_requires_real_deepseek_and_playwright(monkeypatch):
    monkeypatch.setattr(
        linked_runtime,
        "get_llm_runtime_status",
        lambda: {
            "provider": "deepseek",
            "real_llm_enabled": True,
            "ready": True,
            "issues": [],
            "enabled_agents": [
                "market_agent",
                "listing_agent",
                "strategy_agent",
                    "review_agent",
                    "analytics_agent",
                ],
                "react_enabled_agents": ["market_agent", "strategy_agent", "analytics_agent"],
            "fallback_mode": "fail_closed",
        },
    )
    monkeypatch.setattr(
        linked_runtime,
        "get_browser_runtime_status",
        lambda: {
            "backend": "playwright",
            "real_browser_enabled": True,
            "ready": True,
            "issues": [],
        },
    )

    status = linked_runtime.get_linked_runtime_status()

    assert status["ready"] is True
    require_linked_runtime()


def test_linked_runtime_fails_closed_in_rule_mode(monkeypatch):
    monkeypatch.setattr(
        linked_runtime,
        "get_llm_runtime_status",
        lambda: {
            "provider": "deterministic",
            "real_llm_enabled": False,
            "ready": True,
            "issues": [],
            "enabled_agents": [],
            "fallback_mode": "deterministic",
        },
    )
    monkeypatch.setattr(
        linked_runtime,
        "get_browser_runtime_status",
        lambda: {
            "backend": "mock",
            "real_browser_enabled": False,
            "ready": True,
            "issues": [],
        },
    )

    status = linked_runtime.get_linked_runtime_status()

    assert status["ready"] is False
    assert "deepseek_not_enabled" in status["issues"]
    assert "playwright_not_enabled" in status["issues"]
    try:
        require_linked_runtime()
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail["error"] == "linked_runtime_unavailable"
    else:
        raise AssertionError("rule-mode user request must be rejected")


def test_v10_workflow_supports_demo_ui():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert state.agent_outputs["browser_agent"]["verification"]["verified"] is True


def test_ops_ui_separates_current_run_and_task_model_counts():
    assert "本次运行模型调用" in DEMO_HTML
    assert "任务累计模型调用" in DEMO_HTML
    assert "/api/traces/" in DEMO_HTML


def test_v10_regression_report_for_demo(tmp_path: Path):
    report = run_eval(
        Path("data/eval/v9_regression_tasks.json"),
        report_path=tmp_path / "report.json",
    )

    assert report["regression_pass_rate"] == 1.0
    assert report["bad_case_counts"].get("Retrieval Error", 0) == 0
