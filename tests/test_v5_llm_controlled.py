from pathlib import Path

from app.eval.runner import run_eval
from app.model.structured import StructuredOutputError, parse_json_object, require_fields
from app.orchestration.workflow import run_workflow


def test_v5_structured_output_validation():
    payload = parse_json_object('{"title":"A","keywords":[],"bullets":[],"compliance_notes":[]}')
    require_fields(payload, {"title", "keywords", "bullets", "compliance_notes"})


def test_v5_structured_output_rejects_non_json():
    try:
        parse_json_object("not json")
    except StructuredOutputError:
        return
    raise AssertionError("Expected StructuredOutputError")


def test_v5_llm_enabled_listing_strategy_review(monkeypatch):
    monkeypatch.setenv("ECOMPILOT_LLM_AGENTS", "listing_agent,strategy_agent,review_agent")
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert state.agent_outputs["listing_agent"]["generation_mode"] == "llm"
    assert state.agent_outputs["strategy_agent"]["generation_mode"] == "llm"
    assert len(state.model_records) == 3
    assert all(record["provider"] == "deterministic" for record in state.model_records)


def test_v5_eval_reports_model_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("ECOMPILOT_LLM_AGENTS", "listing_agent")
    report = run_eval(
        Path("data/eval/v4_tasks.json"),
        report_path=tmp_path / "report.json",
    )

    assert report["model_call_count"] == 2
