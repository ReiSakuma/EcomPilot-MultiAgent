from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.browser.runtime import get_browser_runtime_status
from app.model.runtime import get_llm_runtime_status


EvidenceStatus = Literal["passed", "failed", "not_run"]


class FinalEvidenceStage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    status: EvidenceStatus
    evidence_path: str
    detail: str


class FinalReleaseStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    release: Literal["v50-final-integration"] = "v50-final-integration"
    project_version: str
    generated_at: datetime
    feature_freeze: bool = True
    interview_ready: bool
    real_external_chain_validated: bool
    production_ready: bool = False
    stages: tuple[FinalEvidenceStage, ...]
    runtime: dict[str, object] = Field(default_factory=dict)
    boundaries: tuple[str, ...]


REPORTS = {
    "offline_core": PROJECT_ROOT / "reports" / "v50" / "offline_acceptance.json",
    "real_browser": PROJECT_ROOT / "reports" / "browser" / "v50" / "report.json",
    "real_deepseek": PROJECT_ROOT / "reports" / "v50" / "live_deepseek_smoke.json",
    "evidence_integrity": PROJECT_ROOT / "reports" / "v50" / "evidence_manifest.json",
}


def build_final_release_status() -> FinalReleaseStatus:
    offline = _stage(
        "offline_core",
        REPORTS["offline_core"],
        passed=lambda value: value.get("passed") is True,
        success="全量回归和最终契约测试已通过。",
        missing="尚未生成 v50 全量回归报告。",
    )
    browser = _stage(
        "real_browser",
        REPORTS["real_browser"],
        passed=lambda value: value.get("passed") is True,
        success="Playwright Chromium 用户界面验收已通过。",
        missing="尚未生成 v50 真实浏览器报告。",
    )
    live = _stage(
        "real_deepseek",
        REPORTS["real_deepseek"],
        passed=_valid_live_deepseek,
        success="已观察到 DeepSeek 完成的真实模型调用。",
        missing="未运行真实 DeepSeek；离线桩不会计入该项。",
    )
    integrity = _stage(
        "evidence_integrity",
        REPORTS["evidence_integrity"],
        passed=lambda value: value.get("valid") is True,
        success="面试证据文件 SHA-256 清单完整。",
        missing="尚未生成 v50 证据哈希清单。",
    )
    stages = (offline, browser, live, integrity)
    return FinalReleaseStatus(
        project_version=PROJECT_VERSION,
        generated_at=datetime.now(timezone.utc),
        interview_ready=all(
            stage.status == "passed" for stage in (offline, browser, integrity)
        ),
        real_external_chain_validated=live.status == "passed",
        stages=stages,
        runtime={
            "llm": _public_runtime(get_llm_runtime_status()),
            "browser": _public_runtime(get_browser_runtime_status()),
        },
        boundaries=(
            "interview_ready 表示本机参考实现证据完整，不代表生产认证。",
            "real_external_chain_validated 只有实际 DeepSeek 成功记录存在时才为 true。",
            "模拟 Seller Center 不是任何真实电商平台生产账户。",
            "SQLite Runtime 是单机多进程参考实现，不是跨主机高可用队列。",
        ),
    )


def _stage(
    name: str,
    path: Path,
    *,
    passed,
    success: str,
    missing: str,
) -> FinalEvidenceStage:
    try:
        relative = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        relative = path.name
    payload = _read_json(path)
    if payload is None:
        return FinalEvidenceStage(
            name=name, status="not_run", evidence_path=relative, detail=missing
        )
    if payload.get("status") == "not_run":
        return FinalEvidenceStage(
            name=name,
            status="not_run",
            evidence_path=relative,
            detail=str(payload.get("error") or payload.get("detail") or missing),
        )
    status: EvidenceStatus = "passed" if passed(payload) else "failed"
    detail = success if status == "passed" else str(
        payload.get("error") or payload.get("detail") or "报告存在，但未满足发布条件。"
    )
    return FinalEvidenceStage(
        name=name, status=status, evidence_path=relative, detail=detail
    )


def _valid_live_deepseek(payload: dict[str, object]) -> bool:
    records = payload.get("model_records")
    return (
        payload.get("status") == "completed"
        and isinstance(records, list)
        and any(
            isinstance(record, dict)
            and record.get("provider") == "deepseek"
            and record.get("status") == "completed"
            for record in records
        )
    )


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _public_runtime(value: dict[str, object]) -> dict[str, object]:
    allowed = {
        "ready",
        "provider",
        "model",
        "real_llm_enabled",
        "real_browser_enabled",
        "backend",
        "issues",
    }
    return {key: item for key, item in value.items() if key in allowed}
