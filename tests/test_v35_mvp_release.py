from __future__ import annotations

from pathlib import Path

from app.eval.mvp_gate import run_mvp_gate
from app.main import release_protocols
from app.release.protocols import build_protocol_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_v36_protocol_manifest_is_complete_and_api_visible() -> None:
    manifest = build_protocol_manifest()
    api_payload = release_protocols()
    versions = {contract.name: contract.version for contract in manifest.contracts}

    assert manifest.release == "v39-chaos-readiness"
    assert manifest.project_version == "0.65.0"
    assert len(manifest.contracts) == 24
    assert versions == {
            "conversation_database": "13",
        "task_state": "1.1",
        "copilot_response": "1.7",
        "handoff": "1.1",
        "artifact": "1.0",
        "market_price_assessment": "market-price-gate-v1",
        "failure_envelope": "1.1",
        "tool_spec": "2.0",
        "reliability": "1.0",
        "a2a": "1.0",
        "sandbox": "1.0",
        "request_compiler": "1.2",
        "route_plan": "1.1",
        "conversation_summary": "2.0",
        "context_budget": "1.0",
        "run_bundle": "2.5",
        "checkpoint_compatibility_diagnostic": "1.0",
        "durable_job_queue": "1.1",
        "worker_lease_fencing": "1.0",
        "execution_saga_outbox": "1.0",
        "worker_bulkhead": "1.0",
        "terminal_outcome": "1.0",
        "chaos_experiment": "1.0",
        "operational_slo": "1.0",
    }
    assert api_payload == manifest.model_dump(mode="json")


def test_v36_frozen_mvp_quality_gate_meets_every_target() -> None:
    report = run_mvp_gate(root=ROOT)

    assert report["passed"] is True
    assert report["checks_passed"] == report["checks_total"]
    assert all(metric["passed"] for metric in report["metrics"].values())
    assert report["metrics"]["intent_accuracy"]["value"] >= 0.95
    assert report["metrics"]["entity_resolution_accuracy"]["value"] >= 0.98
    assert report["metrics"]["silent_ambiguous_selection_count"]["value"] == 0
    assert report["metrics"]["unapproved_write_count"]["value"] == 0
    assert report["metrics"]["cross_tenant_leak_count"]["value"] == 0
    assert report["metrics"]["dangerous_sql_allowed_count"]["value"] == 0


def test_v36_interview_delivery_documents_exist() -> None:
    for relative in (
        "docs/V39_TECHNICAL.md",
        "docs/THREAT_MODEL.md",
        "docs/INTERVIEW_DEMO_SCRIPT.md",
        "docs/BAD_CASES.md",
        "scripts/export_run_bundle.py",
        "scripts/run_v39_mvp_gate.py",
        "scripts/run_v39_operational_acceptance.py",
        "scripts/run_v39_release_visual_check.py",
    ):
        assert (ROOT / relative).is_file(), relative
