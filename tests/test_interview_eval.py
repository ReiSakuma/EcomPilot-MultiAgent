from __future__ import annotations

import json
from pathlib import Path

from app.config import PROJECT_ROOT
from app.eval.interview_runner import run_interview_eval
from app.eval.metadata import build_run_metadata, sanitize_runtime


DATASET = PROJECT_ROOT / "data" / "eval" / "interview_eval_v1.json"


def test_interview_dataset_has_frozen_40_case_distribution() -> None:
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    assert len(cases) == 40
    assert counts == {
        "normal": 10,
        "business_constraint": 8,
        "model_failure": 6,
        "browser_failure": 6,
        "recovery": 5,
        "context_memory": 3,
        "adversarial": 2,
    }


def test_interview_eval_passes_all_expectations(tmp_path: Path) -> None:
    report = run_interview_eval(DATASET, tmp_path / "interview.json")
    assert report["total"] == 40
    assert report["regression_pass_rate"] == 1.0
    assert report["hard_constraint_satisfaction_rate"] == 1.0
    assert report["unauthorized_side_effect_count"] == 0
    assert not report["failure_domain_counts"]


def test_metadata_is_versioned_and_runtime_is_shape_redacted() -> None:
    runtime = {
        "provider": "openai",
        "model": "test-model",
        "api_key_configured": True,
        "api_key": "must-not-leak",
        "authorization": "Bearer must-not-leak",
    }
    sanitized = sanitize_runtime(runtime)
    metadata = build_run_metadata(profile="test", runtime=runtime)
    assert sanitized == {
        "api_key_configured": True,
        "model": "test-model",
        "provider": "openai",
    }
    assert "must-not-leak" not in json.dumps(metadata)
    assert metadata["project_version"] == "0.65.0"
    assert metadata["prompt_version"] == "interview-core-v1"
