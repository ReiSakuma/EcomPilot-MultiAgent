from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Callable, Iterator


PublicEventSink = Callable[[dict[str, Any]], None]

_PUBLIC_EVENT_SINK: ContextVar[PublicEventSink | None] = ContextVar(
    "copilot_public_event_sink", default=None
)

_AGENT_LABELS = {
    "supervisor": "任务编排",
    "market_agent": "市场调研",
    "listing_agent": "商品方案",
    "strategy_agent": "定价与促销",
    "review_agent": "安全审核",
    "browser_agent": "店铺同步",
}

_TOOL_LABELS = {
    "query_market_database": "查询市场数据",
    "build_market_report": "整理市场参考",
    "forecast_demand": "查询需求预测",
    "analyze_competitor_price_trends": "分析竞品价格变化",
    "query_campaign_history": "查询历史活动",
    "calculate_margin": "核算毛利",
    "check_inventory": "核对库存",
    "suggest_discount": "计算候选优惠",
    "browser_execute": "同步模拟店铺",
    "browser_verify": "回读验证店铺结果",
}


@contextmanager
def bind_public_event_sink(sink: PublicEventSink) -> Iterator[None]:
    """Bind one request-scoped event sink to the current execution context."""

    token: Token[PublicEventSink | None] = _PUBLIC_EVENT_SINK.set(sink)
    try:
        yield
    finally:
        _PUBLIC_EVENT_SINK.reset(token)


def publish_trace_event(trace_event: Any) -> None:
    """Project a trace event to a user-safe progress event when a sink is bound.

    The projection deliberately excludes prompts, model output, tool arguments,
    SQL text, policy internals, and reasoning-like fields.
    """

    sink = _PUBLIC_EVENT_SINK.get()
    if sink is None:
        return
    projected = project_trace_event(trace_event)
    if projected is None:
        return
    try:
        sink(projected)
    except Exception:
        # Progress reporting is observability. It must never break the task itself.
        return


def project_trace_event(trace_event: Any) -> dict[str, Any] | None:
    if hasattr(trace_event, "model_dump"):
        event = trace_event.model_dump(mode="json")
    elif isinstance(trace_event, dict):
        event = trace_event
    else:
        return None

    event_type = _value(event.get("event_type"))
    component = str(event.get("component_name") or "system")
    status = _public_status(event.get("status"))
    task_id = str(event.get("task_id") or "") or None
    trace_ref = str(event.get("event_id") or "")
    payload = {"trace_ref": trace_ref} if trace_ref else {}

    if event_type == "plan_created":
        return _event(
            "route_planned", "orchestrator", "completed", "执行路线已确定",
            "系统已拆分本次任务，并开始协调所需能力。", task_id, payload,
        )
    if event_type == "node_started":
        label = _agent_label(component)
        return _event(
            "agent_started", component, "running", f"{label}开始",
            f"{label}正在处理本阶段任务。", task_id, payload,
        )
    if event_type == "agent_completed":
        label = _agent_label(component)
        return _event(
            "agent_completed", component, status, f"{label}{_completion_suffix(status)}",
            f"{label}{_completion_detail(status)}", task_id, payload,
        )
    if event_type == "model_call":
        label = _agent_label(component)
        return _event(
            "model_completed", component, status, "模型生成已返回",
            f"{label}已完成一次受约束的模型生成。" if status != "failed"
            else f"{label}的模型调用未完成，系统正在执行恢复策略。",
            task_id, payload,
        )
    if event_type == "tool_call":
        label = _TOOL_LABELS.get(component, component.replace("_", " "))
        return _event(
            "tool_completed", component, status, label,
            f"{label}{_completion_detail(status)}", task_id, payload,
        )
    if event_type == "semantic_correction":
        return _event(
            "review_revised", component, "completed", "方案已自动修正",
            "系统已移除与已确认事实或规则不一致的内容，并记录修正依据。",
            task_id, payload,
        )
    if event_type == "approval_waiting":
        return _event(
            "approval_waiting", "approval", "waiting", "等待你的确认",
            "方案已完成检查，确认前不会写入店铺。", task_id, payload,
        )
    if event_type == "error":
        return _event(
            "stage_failed", component, "failed", "阶段遇到问题",
            "系统正在按受控重试、修正或降级策略处理。", task_id, payload,
        )
    return None


def _event(
    event_type: str,
    stage: str,
    status: str,
    title: str,
    detail: str,
    task_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "stage": stage,
        "status": status,
        "title": title,
        "detail": detail,
        "task_id": task_id,
        "payload": payload,
    }


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _public_status(value: Any) -> str:
    normalized = str(value or "completed").lower()
    if normalized in {"failed", "error", "blocked", "denied"}:
        return "failed"
    if normalized in {"running", "started", "pending"}:
        return "running"
    if normalized in {"waiting", "waiting_for_approval"}:
        return "waiting"
    return "completed"


def _agent_label(component: str) -> str:
    return _AGENT_LABELS.get(component, component.replace("_", " "))


def _completion_suffix(status: str) -> str:
    return "未完成" if status == "failed" else "完成"


def _completion_detail(status: str) -> str:
    return "未完成，系统正在处理。" if status == "failed" else "已完成。"
