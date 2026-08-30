from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.release.catalog import THREAT_CONTROLS, build_threat_model
from app.release.evidence import (
    REQUIRED_EVIDENCE_PATHS,
    build_evidence_manifest,
    verify_evidence_manifest,
    write_evidence_manifest,
)
from app.release.readiness import build_release_readiness


ROOT = Path(__file__).resolve().parents[1]
RAW_REPORT = ROOT / "reports" / "raw" / "v26_final_acceptance.json"
SUMMARY_REPORT = ROOT / "reports" / "summaries" / "V26_FINAL_ACCEPTANCE.md"


def main() -> None:
    threats = build_threat_model()
    referenced_paths = {
        path
        for control in THREAT_CONTROLS
        for path in (*control.evidence_paths, *control.test_paths)
    }
    route_paths = {route.path for route in app.routes}
    readiness_before_gate = build_release_readiness().model_dump(mode="json")
    checks = {
        "ten_threat_controls_are_cataloged": threats["controls_total"] == 10,
        "threat_ids_are_unique": len({control.threat_id for control in THREAT_CONTROLS}) == len(THREAT_CONTROLS),
        "every_threat_has_multiple_control_layers": all(len(control.control_layers) >= 2 for control in THREAT_CONTROLS),
        "every_threat_has_test_evidence": all(control.test_paths for control in THREAT_CONTROLS),
        "all_catalog_references_exist": all((ROOT / path).is_file() for path in referenced_paths),
        "every_claim_has_an_honest_boundary": all(len(control.boundary) >= 20 for control in THREAT_CONTROLS),
        "final_documents_exist": all((ROOT / path).is_file() for path in (
            "README.md", "V26_技术文档.md", "docs/THREAT_MODEL.md", "docs/INTERVIEW_DEMO_SCRIPT.md"
        )),
        "release_api_surface_is_complete": {
            "/api/release/readiness", "/api/release/threat-model", "/api/release/evidence"
        }.issubset(route_paths),
        "feature_scope_is_frozen": readiness_before_gate["feature_freeze"] is True,
        "production_readiness_is_not_overclaimed": readiness_before_gate["production_readiness"]["ready"] is False,
        "external_integrations_are_reported_separately": {
            "llm", "browser"
        } == set(readiness_before_gate["external_integrations"]),
        "evidence_manifest_integrity": False,
    }
    _write_reports(checks)
    manifest = build_evidence_manifest(paths=REQUIRED_EVIDENCE_PATHS)
    write_evidence_manifest(manifest)
    checks["evidence_manifest_integrity"] = bool(verify_evidence_manifest(manifest)["valid"])
    _write_reports(checks)
    manifest = build_evidence_manifest(paths=REQUIRED_EVIDENCE_PATHS)
    write_evidence_manifest(manifest)
    final_integrity = verify_evidence_manifest(manifest)
    checks["evidence_manifest_integrity"] = bool(final_integrity["valid"])
    report = _write_reports(checks, integrity=final_integrity)
    # The report is evidence too, so seal its final bytes after the last report write.
    manifest = build_evidence_manifest(paths=REQUIRED_EVIDENCE_PATHS)
    write_evidence_manifest(manifest)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


def _write_reports(
    checks: dict[str, bool],
    *,
    integrity: dict[str, object] | None = None,
) -> dict[str, object]:
    passed_count = sum(checks.values())
    report = {
        "version": "v26-interview-final",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()),
        "checks_passed": passed_count,
        "checks_total": len(checks),
        "checks": checks,
        "evidence_integrity": integrity or {},
        "feature_freeze": True,
        "production_ready": False,
        "boundary": (
            "V26 is the interview-final feature freeze, not a production commerce deployment. "
            "External IdP, shared transactional state, infrastructure sandboxing and a real "
            "commerce platform integration remain production work."
        ),
    }
    RAW_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_REPORT.parent.mkdir(parents=True, exist_ok=True)
    RAW_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(
        f"- [{'x' if passed else ' '}] `{name}`" for name, passed in checks.items()
    )
    SUMMARY_REPORT.write_text(
        "# V26 Final Acceptance\n\n"
        f"- Status: **{'passed' if report['passed'] else 'failed'}**\n"
        f"- Checks: **{passed_count}/{len(checks)}**\n"
        "- Scope: interview-final feature freeze\n"
        "- Production ready: **no**\n\n"
        "## Checks\n\n"
        f"{rows}\n\n"
        "## Boundary\n\n"
        f"{report['boundary']}\n",
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    main()
