from __future__ import annotations

import json
from pathlib import Path

from app.copilot_ui import COPILOT_HTML
from app.main import app
from app.release import final


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _redirect_reports(monkeypatch, root: Path) -> dict[str, Path]:
    paths = {
        name: root / path.name
        for name, path in final.REPORTS.items()
    }
    for name, path in paths.items():
        monkeypatch.setitem(final.REPORTS, name, path)
    return paths


def test_final_status_never_claims_missing_external_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    _redirect_reports(monkeypatch, tmp_path)

    status = final.build_final_release_status()

    assert status.interview_ready is False
    assert status.real_external_chain_validated is False
    assert {stage.status for stage in status.stages} == {"not_run"}


def test_final_status_separates_interview_and_live_readiness(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _redirect_reports(monkeypatch, tmp_path)
    _write(paths["offline_core"], {"passed": True})
    _write(paths["real_browser"], {"passed": True})
    _write(paths["evidence_integrity"], {"valid": True})

    status = final.build_final_release_status()

    assert status.interview_ready is True
    assert status.real_external_chain_validated is False
    assert next(
        stage for stage in status.stages if stage.name == "real_deepseek"
    ).status == "not_run"


def test_explicit_not_run_deepseek_report_remains_not_run(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _redirect_reports(monkeypatch, tmp_path)
    _write(paths["offline_core"], {"passed": True})
    _write(paths["real_browser"], {"passed": True})
    _write(paths["evidence_integrity"], {"valid": True})
    _write(
        paths["real_deepseek"],
        {
            "status": "not_run",
            "provider": "deepseek",
            "error": "DEEPSEEK_API_KEY is not configured",
        },
    )

    status = final.build_final_release_status()
    live = next(stage for stage in status.stages if stage.name == "real_deepseek")

    assert live.status == "not_run"
    assert status.real_external_chain_validated is False


def test_only_completed_deepseek_records_validate_the_live_chain(
    tmp_path: Path, monkeypatch
) -> None:
    paths = _redirect_reports(monkeypatch, tmp_path)
    for name in ("offline_core", "real_browser"):
        _write(paths[name], {"passed": True})
    _write(paths["evidence_integrity"], {"valid": True})
    _write(
        paths["real_deepseek"],
        {
            "status": "completed",
            "model_records": [
                {"provider": "deepseek", "status": "completed"}
            ],
        },
    )

    status = final.build_final_release_status()

    assert status.interview_ready is True
    assert status.real_external_chain_validated is True
    assert status.production_ready is False


def test_stub_model_record_is_not_live_evidence(tmp_path: Path, monkeypatch) -> None:
    paths = _redirect_reports(monkeypatch, tmp_path)
    _write(
        paths["real_deepseek"],
        {
            "status": "completed",
            "model_records": [
                {"provider": "deterministic", "status": "completed"}
            ],
        },
    )

    status = final.build_final_release_status()

    assert status.real_external_chain_validated is False
    assert next(
        stage for stage in status.stages if stage.name == "real_deepseek"
    ).status == "failed"


def test_v50_api_and_user_facing_status_are_exposed() -> None:
    paths = {route.path for route in app.routes}

    assert "/api/release/final" in paths
    assert 'data-example="listing"' in COPILOT_HTML
    assert 'data-example="market"' in COPILOT_HTML
    assert 'data-example="analytics"' in COPILOT_HTML
    assert 'id="executionReceipt"' in COPILOT_HTML
    assert "页面刷新后会自动恢复进度" in COPILOT_HTML
