from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.model.promotion_migration import migrate_checkpoint_payload
from app.orchestration.state import TaskState


CompatibilityStatus = Literal["compatible", "migrated", "requires_regeneration"]


class CompatibilityDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    status: CompatibilityStatus
    source_schema_version: str
    target_schema_version: Literal["1.1"] = "1.1"
    task_id: str | None = None
    migrations: tuple[dict[str, Any], ...] = ()
    reason_code: str
    user_message: str
    recovery_action: Literal[
        "continue",
        "continue_with_migrated_view",
        "regenerate_task_from_conversation",
    ]


def diagnose_checkpoint_payload(payload: dict[str, Any]) -> CompatibilityDiagnostic:
    """Validate old state without rewriting it and return an actionable result."""

    source = deepcopy(payload)
    source_schema = str(source.get("schema_version") or "unknown")
    task_id = str(source.get("task_id")) if source.get("task_id") else None
    try:
        migrated = migrate_checkpoint_payload(source)
        state = TaskState.model_validate(migrated)
    except (TypeError, ValueError) as exc:
        return CompatibilityDiagnostic(
            status="requires_regeneration",
            source_schema_version=source_schema,
            task_id=task_id,
            reason_code="checkpoint_contract_incompatible",
            user_message=(
                "这条旧任务的数据协议无法安全恢复。原会话不会删除，请从会话历史重新生成任务。"
            ),
            recovery_action="regenerate_task_from_conversation",
            migrations=({"error_type": type(exc).__name__, "detail": str(exc)[:500]},),
        )

    migrations = tuple(state.protocol_migrations)
    changed = any(
        item.get("status") == "migrated"
        or item.get("reason_code")
        not in {"already_typed", "no_legacy_promotion_fields"}
        for item in migrations
    )
    if changed or source_schema != state.schema_version:
        return CompatibilityDiagnostic(
            status="migrated",
            source_schema_version=source_schema,
            task_id=state.task_id,
            migrations=migrations,
            reason_code="compatible_read_only_migration_applied",
            user_message="旧任务已通过只读迁移恢复，原始 Checkpoint 没有被覆盖。",
            recovery_action="continue_with_migrated_view",
        )
    return CompatibilityDiagnostic(
        status="compatible",
        source_schema_version=source_schema,
        task_id=state.task_id,
        migrations=migrations,
        reason_code="current_contract",
        user_message="任务协议与 v59 兼容，可以继续读取或恢复。",
        recovery_action="continue",
    )
