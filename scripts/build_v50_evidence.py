from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.release.final import build_final_release_status  # noqa: E402


OUTPUT_DIR = ROOT / "reports" / "v50"
MANIFEST = OUTPUT_DIR / "evidence_manifest.json"
SUMMARY = OUTPUT_DIR / "FINAL_INTERVIEW_SUMMARY.md"
ARCHIVE = OUTPUT_DIR / "EcomPilot_v50_interview_evidence.zip"

REQUIRED = (
    "README.md",
    "docs/V50_FINAL_TECHNICAL.md",
    "docs/THREAT_MODEL.md",
    "docs/INTERVIEW_DEMO_SCRIPT.md",
    "app/release/final.py",
    "app/release/protocols.py",
    "app/copilot/compiler.py",
    "app/copilot/batch_jobs.py",
    "app/copilot_ui.py",
    "app/distributed/runtime.py",
    "app/orchestration/react_loop.py",
    "app/orchestration/a2a.py",
    "app/sql/policy.py",
    "app/sandbox/runner.py",
    "app/main.py",
    "tests/test_v40_task_identity.py",
    "tests/test_v41_semantic_compiler.py",
    "tests/test_v42_task_routing.py",
    "tests/test_v43_task_checkpoints.py",
    "tests/test_v44_batch_compiler.py",
    "tests/test_v45_batch_orchestration.py",
    "tests/test_v46_batch_execution.py",
    "tests/test_v47_batch_recovery.py",
    "tests/test_v48_batch_dispatch.py",
    "tests/test_v49_batch_receipt_recovery.py",
    "tests/test_v50_final_release.py",
    "reports/v50/offline_acceptance.json",
    "reports/browser/v50/report.json",
)
OPTIONAL = (
    "reports/v50/live_deepseek_smoke.json",
    "reports/browser/v50/user_desktop.png",
    "reports/browser/v50/user_mobile.png",
    "reports/browser/v50/ops.png",
    "reports/browser/v50/traces.png",
    "reports/browser/v50/seller_center.png",
)
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(rb"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    selected = [path for path in (*REQUIRED, *OPTIONAL) if (ROOT / path).is_file()]
    unsafe = _secret_findings(selected)
    entries = [_entry(path) for path in sorted(selected)]
    payload = {
        "release": "v50-final-integration",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": "sha256",
        "valid": not missing and not unsafe,
        "entry_count": len(entries),
        "missing": missing,
        "secret_findings": unsafe,
        "entries": entries,
    }
    MANIFEST.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    status = build_final_release_status()
    SUMMARY.write_text(_summary(status), encoding="utf-8")
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in selected:
            archive.write(ROOT / relative, arcname=relative)
        archive.write(MANIFEST, arcname=str(MANIFEST.relative_to(ROOT)))
        archive.write(SUMMARY, arcname=str(SUMMARY.relative_to(ROOT)))
    result = {
        **payload,
        "archive": str(ARCHIVE.relative_to(ROOT)),
        "summary": str(SUMMARY.relative_to(ROOT)),
        "interview_ready": status.interview_ready,
        "real_external_chain_validated": status.real_external_chain_validated,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not payload["valid"]:
        raise SystemExit(1)


def _entry(relative: str) -> dict[str, object]:
    payload = (ROOT / relative).read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _secret_findings(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for relative in paths:
        if not relative.startswith("reports/"):
            continue
        payload = (ROOT / relative).read_bytes()
        if any(pattern.search(payload) for pattern in SECRET_PATTERNS):
            findings.append(relative)
    return findings


def _summary(status) -> str:
    rows = "\n".join(
        f"| {stage.name} | {stage.status} | `{stage.evidence_path}` | {stage.detail} |"
        for stage in status.stages
    )
    return f"""# EcomPilot v50 面试证据摘要

- 项目版本：`{status.project_version}`
- 面试参考实现就绪：`{str(status.interview_ready).lower()}`
- 真实 DeepSeek 链路已验证：`{str(status.real_external_chain_validated).lower()}`
- 生产就绪：`false`

| 阶段 | 状态 | 证据 | 说明 |
|---|---|---|---|
{rows}

## 建议演示顺序

1. 用户工作台展示自然语言、多任务会话和结果面板。
2. 运维后台展示 Agent、工具、队列、权限和失败协议。
3. Trace 页面展示模型调用与工具证据。
4. 模拟商家后台展示执行后的业务状态。
5. 用本清单说明哪些是本机真实执行，哪些仍是模拟边界。
"""


if __name__ == "__main__":
    main()
