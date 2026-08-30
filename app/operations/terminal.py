from __future__ import annotations

from app.operations.models import TerminalOutcome
from app.orchestration.failures import FailureEnvelope, TaskOutcome


def project_terminal_outcome(
    outcome: TaskOutcome | str,
    *,
    degradation_refs: tuple[str, ...] = (),
    failure: FailureEnvelope | None = None,
) -> TerminalOutcome:
    """Collapse internal states into the five outcomes an operator can act on."""

    source = outcome.value if isinstance(outcome, TaskOutcome) else str(outcome)
    if source == TaskOutcome.completed.value:
        terminal = "degraded_completed" if degradation_refs else "success"
        return TerminalOutcome(
            terminal_class=terminal,
            source_outcome=source,
            reason=(
                "主任务完成，部分非关键能力已按策略降级。"
                if degradation_refs
                else "主任务及必要验证均已完成。"
            ),
            recoverable=bool(degradation_refs),
            degradation_refs=degradation_refs,
        )
    if source in {
        TaskOutcome.awaiting_approval.value,
        TaskOutcome.waiting_for_input.value,
    }:
        return TerminalOutcome(
            terminal_class="waiting_user",
            source_outcome=source,
            reason="继续执行需要用户补充信息或确认高风险操作。",
            recoverable=True,
            human_action="补充缺失信息或确认待执行方案",
        )
    if source == TaskOutcome.business_rejected.value:
        return TerminalOutcome(
            terminal_class="business_rejected",
            source_outcome=source,
            reason=failure.user_message if failure else "业务规则明确拒绝本次执行。",
            recoverable=bool(failure and failure.recoverable),
            human_action="调整业务条件后创建新任务",
            failure_code=failure.code if failure else None,
        )
    if source in {
        TaskOutcome.technical_failed.value,
        TaskOutcome.needs_attention.value,
    }:
        return TerminalOutcome(
            terminal_class="manual_attention",
            source_outcome=source,
            reason=failure.user_message if failure else "自动恢复预算已耗尽，需要人工处理。",
            recoverable=False,
            human_action="查看 Run Bundle 并由运维人员处理",
            failure_code=failure.code if failure else None,
        )
    raise ValueError(f"'{source}' is not a terminal task outcome")
