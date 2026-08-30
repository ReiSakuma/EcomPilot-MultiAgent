from __future__ import annotations

from pathlib import Path

import pytest

from app.demo_ui import DEMO_HTML
from app.main import release_evidence, release_readiness, release_threat_model
from app.release.catalog import THREAT_CONTROLS, build_threat_model
from app.release.evidence import build_evidence_manifest, verify_evidence_manifest
from app.release.readiness import build_release_readiness


ROOT = Path(__file__).resolve().parents[1]


def test_threat_catalog_has_unique_complete_control_records() -> None:
    catalog = build_threat_model()

    assert catalog["controls_total"] == 13
    assert catalog["coverage_rate"] == 1.0
    assert len({control.threat_id for control in THREAT_CONTROLS}) == 13
    assert all(control.control_layers for control in THREAT_CONTROLS)
    assert all(control.boundary for control in THREAT_CONTROLS)


def test_all_catalog_evidence_and_test_paths_exist() -> None:
    paths = {
        path
        for control in THREAT_CONTROLS
        for path in (*control.evidence_paths, *control.test_paths)
    }

    assert paths
    assert all((ROOT / path).is_file() for path in paths)


def test_manifest_detects_changed_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "proof.txt"
    evidence.write_text("trusted", encoding="utf-8")
    manifest = build_evidence_manifest(root=tmp_path, paths=("proof.txt",))

    assert verify_evidence_manifest(manifest, root=tmp_path)["valid"] is True
    evidence.write_text("changed", encoding="utf-8")
    result = verify_evidence_manifest(manifest, root=tmp_path)
    assert result["valid"] is False
    assert result["changed"] == ["proof.txt"]


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes project root"):
        build_evidence_manifest(root=tmp_path, paths=("../outside.txt",))


def test_readiness_freezes_features_without_claiming_production() -> None:
    readiness = build_release_readiness()

    assert readiness.scope == "interview_final"
    assert readiness.feature_freeze is True
    assert readiness.visual_gate["passed"] is True
    assert readiness.production_readiness["ready"] is False
    assert set(readiness.external_integrations) == {"llm", "browser"}


def test_release_api_functions_share_the_same_release_contract() -> None:
    readiness = release_readiness()
    threats = release_threat_model()
    evidence = release_evidence()

    assert readiness["release"] == threats["release"] == "v39-chaos-readiness"
    assert threats["controls_total"] == 13
    assert "integrity" in evidence


def test_ops_ui_has_release_evidence_without_a_production_claim() -> None:
    assert "showTab('release'" in DEMO_HTML
    assert "/api/release/readiness" in DEMO_HTML
    assert "/api/release/threat-model" in DEMO_HTML
    assert "/api/release/evidence" in DEMO_HTML
    assert "未声明生产就绪" in DEMO_HTML
    assert "showTab('resilience'" in DEMO_HTML
    assert "/api/operations/readiness" in DEMO_HTML
