from __future__ import annotations

import re

from app.conversations.models import (
    PendingRequestRecord,
    TaskRouteDecision,
    TaskSessionRecord,
)


_RECALL = re.compile(r"(之前|上次|先前|还记得|当时|历史).{0,12}(任务|方案|讨论|商品)")
_SWITCH = re.compile(r"(切换|回到|继续处理|接着处理|恢复).{0,16}(任务|方案|商品|上架)")
_CONTINUE = re.compile(r"^(继续|补充|确认|可以|是的|不是|改成|改为|价格|售价|成本|库存|毛利)")
_NEW_TASK = re.compile(
    r"(我要|我想|请|帮我|麻烦).{0,8}(上架|创建|新增|查询|调研|分析|修改|查看)"
    r"|(上架|创建|新增|查询|调研|分析|修改|查看).{0,10}(商品|产品|市场|销量|销售|库存|耳机|键盘)"
)


class TaskRelationRouter:
    """Small control-plane router; it never extracts business values."""

    def route(
        self,
        message: str,
        *,
        sessions: list[TaskSessionRecord],
        active_task_session_id: str | None,
        pending: PendingRequestRecord | None,
    ) -> TaskRouteDecision:
        text = " ".join(message.strip().split())
        active = next(
            (item for item in sessions if item.task_session_id == active_task_session_id),
            None,
        )
        referenced = self._match_referenced_session(text, sessions)

        if _RECALL.search(text):
            target = referenced or self._previous_session(sessions, active_task_session_id)
            return TaskRouteDecision(
                relation="recall_task",
                target_task_session_id=target.task_session_id if target else None,
                confidence=0.9 if target else 0.62,
                reason="用户明确引用历史任务。",
                evidence=["history_reference"] + ([target.title] if target else []),
            )
        if _SWITCH.search(text):
            target = referenced or self._previous_session(sessions, active_task_session_id)
            return TaskRouteDecision(
                relation="switch_task",
                target_task_session_id=target.task_session_id if target else None,
                confidence=0.91 if target else 0.6,
                reason="用户明确要求切换或恢复任务。",
                evidence=["switch_expression"] + ([target.title] if target else []),
            )
        if pending is not None:
            if _NEW_TASK.search(text) and not _CONTINUE.search(text):
                return TaskRouteDecision(
                    relation="new_task",
                    confidence=0.9,
                    reason="当前存在待补充任务，但本轮包含新的完整业务动作。",
                    evidence=["pending_exists", "new_business_action"],
                )
            return TaskRouteDecision(
                relation="continue_task",
                target_task_session_id=pending.task_session_id or active_task_session_id,
                confidence=0.86,
                reason="本轮用于回答当前任务的待补充问题。",
                evidence=["pending_clarification", pending.last_question[:80]],
            )
        if _NEW_TASK.search(text):
            return TaskRouteDecision(
                relation="new_task",
                confidence=0.88,
                reason="识别到一个新的业务请求。",
                evidence=["new_business_action"],
            )
        if active is not None and _CONTINUE.search(text):
            return TaskRouteDecision(
                relation="continue_task",
                target_task_session_id=active.task_session_id,
                confidence=0.78,
                reason="本轮显式延续当前任务。",
                evidence=["continuation_expression", active.title],
            )
        return TaskRouteDecision(
            relation="general_message",
            confidence=0.7,
            reason="未发现可可靠绑定到业务任务的关系。",
            evidence=[],
            source="fallback",
        )

    @staticmethod
    def _previous_session(
        sessions: list[TaskSessionRecord], active_task_session_id: str | None
    ) -> TaskSessionRecord | None:
        return next(
            (item for item in sessions if item.task_session_id != active_task_session_id),
            sessions[0] if sessions else None,
        )

    @staticmethod
    def _match_referenced_session(
        message: str, sessions: list[TaskSessionRecord]
    ) -> TaskSessionRecord | None:
        meaningful = re.findall(r"[\u4e00-\u9fff]{2,8}", message)
        candidates = [word for word in meaningful if word not in {"之前", "上次", "任务", "商品", "方案"}]
        for session in sessions:
            if any(word in session.title for word in candidates):
                return session
        return None
