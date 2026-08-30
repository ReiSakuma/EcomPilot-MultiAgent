from __future__ import annotations

import json
from pathlib import Path

from app.browser.runtime import get_browser_runtime_status
from app.config import PROJECT_ROOT
from app.model.runtime import get_llm_runtime_status
from app.release.catalog import build_threat_model
from app.release.evidence import current_evidence_status
from app.release.models import ReleaseReadiness


FINAL_GATE_PATH = PROJECT_ROOT / "reports" / "raw" / "v39_mvp_gate.json"
RELIABILITY_GATE_PATH = PROJECT_ROOT / "reports" / "raw" / "v39_operational_readiness.json"
VISUAL_GATE_PATH = PROJECT_ROOT / "reports" / "browser" / "v39" / "visual_report.json"


def build_release_readiness() -> ReleaseReadiness:
    gate = _load_gate(FINAL_GATE_PATH)
    reliability = _load_operational_gate(RELIABILITY_GATE_PATH)
    visual = _load_visual_gate(VISUAL_GATE_PATH)
    threats = build_threat_model()
    evidence = current_evidence_status()
    llm = get_llm_runtime_status()
    browser = get_browser_runtime_status()
    core_passed = bool(gate.get("passed"))
    evidence_valid = bool(evidence.get("valid"))
    coverage_complete = threats["coverage_rate"] == 1.0
    visual_passed = bool(visual.get("passed"))
    reliability_passed = reliability.get("status") == "reference_validated"
    interview_ready = (
        core_passed
        and reliability_passed
        and visual_passed
        and evidence_valid
        and coverage_complete
    )
    return ReleaseReadiness(
        release="v39-chaos-readiness",
        status="interview_ready" if interview_ready else "needs_validation",
        feature_freeze=True,
        core_gate={
            "passed": core_passed,
            "checks_passed": gate.get("checks_passed", 0),
            "checks_total": gate.get("checks_total", 0),
            "report_path": str(FINAL_GATE_PATH.relative_to(PROJECT_ROOT)),
        },
        reliability_gate={
            "passed": reliability_passed,
            "scenarios_passed": (reliability.get("chaos") or {}).get("recovered_scenarios", 0),
            "scenarios_total": (reliability.get("chaos") or {}).get("total_scenarios", 0),
            "duplicate_writes": (reliability.get("capacity") or {}).get("duplicate_jobs"),
            "report_path": str(RELIABILITY_GATE_PATH.relative_to(PROJECT_ROOT)),
        },
        operational_gate={
            "passed": reliability_passed,
            "slo": (reliability.get("slo") or {}).get("passed", False),
            "tenant_isolation": (reliability.get("isolation") or {}).get("passed", False),
            "capacity": (reliability.get("capacity") or {}).get("passed", False),
            "five_terminal_states": reliability.get("five_terminal_states_covered", False),
        },
        visual_gate={
            "passed": visual_passed,
            "viewports": len(visual.get("results") or []),
            "console_errors": len(visual.get("console_errors") or []),
            "report_path": str(VISUAL_GATE_PATH.relative_to(PROJECT_ROOT)),
        },
        quality_metrics=dict(gate.get("metrics") or {}),
        run_bundle={
            "version": "2.4",
            "exporter": "scripts/export_run_bundle.py",
            "integrity": "sha256_entry_manifest",
        },
        threat_coverage={
            "passed": coverage_complete,
            "controls_total": threats["controls_total"],
            "coverage_rate": threats["coverage_rate"],
        },
        evidence_integrity=evidence,
        external_integrations={
            "llm": {
                "mode": "real_provider" if llm.get("provider") != "deterministic" else "demo_mode",
                "provider": llm.get("provider"),
                "model": llm.get("model"),
                "ready": bool(llm.get("ready")),
                "required_for_offline_gate": False,
            },
            "browser": {
                "mode": "real_browser" if browser.get("backend") == "playwright" else "demo_mode",
                "backend": browser.get("backend"),
                "ready": bool(browser.get("ready")),
                "required_for_offline_gate": False,
            },
        },
        production_readiness={
            "ready": False,
            "status": "not_claimed",
            "reasons": [
                "静态演示身份尚未替换为生产 IdP/OIDC",
                "SQLite 并发协议需要在生产环境迁移为 PostgreSQL/Redis/消息队列集群",
                "熔断状态仍是单进程内存，尚未实现跨实例协调",
                "进程沙盒尚未升级为容器或虚拟机级隔离",
                "Seller Center 是项目内模拟店铺而非真实平台账户",
            ],
        },
    )


def _load_gate(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"passed": False, "reason": "final_gate_not_run"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"passed": False, "reason": "invalid_final_gate_report"}
    if payload.get("version") != "v39-chaos-readiness":
        return {"passed": False, "reason": "wrong_release_report"}
    return payload


def _load_visual_gate(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"passed": False, "reason": "visual_gate_not_run"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"passed": False, "reason": "invalid_visual_gate_report"}
    if payload.get("version") != "v39-chaos-readiness":
        return {"passed": False, "reason": "wrong_visual_report"}
    return payload


def _load_operational_gate(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"status": "needs_validation", "reason": "operational_gate_not_run"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "needs_validation", "reason": "invalid_operational_report"}
    if payload.get("release") != "v39-chaos-readiness":
        return {"status": "needs_validation", "reason": "wrong_operational_report"}
    return payload
