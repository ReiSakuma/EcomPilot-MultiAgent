from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT
from app.release.models import EvidenceEntry, EvidenceManifest


EVIDENCE_MANIFEST_PATH = PROJECT_ROOT / "reports" / "evidence" / "V39_EVIDENCE_MANIFEST.json"
REQUIRED_EVIDENCE_PATHS: tuple[str, ...] = (
    "README.md",
    "docs/V39_TECHNICAL.md",
    "docs/THREAT_MODEL.md",
    "docs/INTERVIEW_DEMO_SCRIPT.md",
    "docs/BAD_CASES.md",
    "reports/raw/v39_mvp_gate.json",
    "reports/raw/v39_chaos_acceptance.json",
    "reports/raw/v39_capacity_acceptance.json",
    "reports/raw/v39_isolation_audit.json",
    "reports/raw/v39_slo_report.json",
    "reports/raw/v39_operational_readiness.json",
    "reports/browser/v39/visual_report.json",
    "reports/browser/v39/user_ui_layout/report.json",
    "app/release/protocols.py",
    "app/eval/mvp_gate.py",
    "data/eval/v35_mvp_cases.json",
    "app/orchestration/react_loop.py",
    "app/orchestration/planner.py",
    "app/agents/market.py",
    "app/agents/strategy.py",
    "app/tools/market_data.py",
    "app/tools/product_tools.py",
    "app/tools/strategy_evidence_tools.py",
    "data/strategy/strategy_evidence.json",
    "docs/STRATEGY_REACT_EVIDENCE.md",
    "app/orchestration/executor.py",
    "app/reliability/models.py",
    "app/reliability/classifier.py",
    "app/reliability/circuit_breaker.py",
    "app/reliability/dead_letter.py",
    "app/orchestration/reducer.py",
    "app/model/adapter.py",
    "app/safety/policy_gateway.py",
    "app/orchestration/a2a.py",
    "app/copilot/compiler.py",
    "app/main.py",
    "app/safety/preflight.py",
    "app/safety/content_revision.py",
    "app/security/capability_tokens.py",
    "app/sql/policy.py",
    "app/sandbox/runner.py",
    "app/access/policy.py",
    "app/browser/tickets.py",
    "app/release/catalog.py",
    "app/distributed/runtime.py",
    "app/distributed/bulkhead.py",
    "app/operations/models.py",
    "app/operations/terminal.py",
    "app/operations/chaos.py",
    "app/operations/assessment.py",
    "app/copilot_ui.py",
    "scripts/export_run_bundle.py",
    "scripts/run_v39_mvp_gate.py",
    "scripts/run_v39_operational_acceptance.py",
    "scripts/run_v39_release_visual_check.py",
    "scripts/run_v39_user_ui_layout_check.py",
    "tests/test_v19_react_loop.py",
    "tests/test_strategy_evidence_react.py",
    "tests/test_v17_tool_calling.py",
    "tests/test_v21_text_to_sql.py",
    "tests/test_v22_capability_security.py",
    "tests/test_v23_process_sandbox.py",
    "tests/test_v24_tenant_access.py",
    "tests/test_v25_tenant_execution.py",
    "tests/test_v26_final_release.py",
    "tests/test_v36_reliability.py",
    "tests/test_v37_multi_intent_context.py",
    "tests/test_v38_distributed_runtime.py",
    "tests/test_v39_operational_readiness.py",
    "tests/test_v39_user_ui_layout.py",
    "tests/test_v39_preflight_stability.py",
)


def build_evidence_manifest(
    *,
    root: Path = PROJECT_ROOT,
    paths: tuple[str, ...] = REQUIRED_EVIDENCE_PATHS,
) -> EvidenceManifest:
    entries = tuple(_entry(root, relative_path) for relative_path in sorted(set(paths)))
    return EvidenceManifest(
        release="v39-chaos-readiness",
        generated_at=datetime.now(timezone.utc),
        entries=entries,
    )


def write_evidence_manifest(
    manifest: EvidenceManifest,
    *,
    path: Path = EVIDENCE_MANIFEST_PATH,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_evidence_manifest(*, path: Path = EVIDENCE_MANIFEST_PATH) -> EvidenceManifest | None:
    if not path.exists():
        return None
    try:
        return EvidenceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def verify_evidence_manifest(
    manifest: EvidenceManifest,
    *,
    root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    changed: list[str] = []
    missing: list[str] = []
    unsafe: list[str] = []
    for expected in manifest.entries:
        try:
            actual = _entry(root, expected.path)
        except FileNotFoundError:
            missing.append(expected.path)
            continue
        except ValueError:
            unsafe.append(expected.path)
            continue
        if actual.sha256 != expected.sha256 or actual.size_bytes != expected.size_bytes:
            changed.append(expected.path)
    valid = not changed and not missing and not unsafe
    return {
        "valid": valid,
        "entry_count": len(manifest.entries),
        "changed": changed,
        "missing": missing,
        "unsafe": unsafe,
        "algorithm": manifest.algorithm,
    }


def current_evidence_status() -> dict[str, object]:
    manifest = load_evidence_manifest()
    if manifest is None:
        return {
            "valid": False,
            "entry_count": 0,
            "changed": [],
            "missing": [str(EVIDENCE_MANIFEST_PATH.relative_to(PROJECT_ROOT))],
            "unsafe": [],
            "algorithm": "sha256",
        }
    result = verify_evidence_manifest(manifest)
    result["manifest_path"] = str(EVIDENCE_MANIFEST_PATH.relative_to(PROJECT_ROOT))
    result["generated_at"] = manifest.generated_at.isoformat()
    return result


def _entry(root: Path, relative_path: str) -> EvidenceEntry:
    path = _safe_path(root, relative_path)
    if not path.is_file():
        raise FileNotFoundError(relative_path)
    payload = path.read_bytes()
    return EvidenceEntry(
        path=relative_path,
        kind=_kind(relative_path),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _safe_path(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("Evidence path must be project-relative")
    resolved_root = root.resolve()
    candidate = (resolved_root / relative_path).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("Evidence path escapes project root")
    return candidate


def _kind(path: str) -> str:
    if path.startswith("tests/"):
        return "test"
    if path.startswith("reports/"):
        return "report"
    if path.startswith("docs/") or path.endswith(".md"):
        return "document"
    return "code"
