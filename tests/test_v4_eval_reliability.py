from pathlib import Path

from app.eval.runner import run_eval
from app.model.adapter import ModelAdapter
from app.orchestration.workflow import run_workflow


def test_v4_records_tool_calls():
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert state.tool_records
    assert any(record["tool_name"] == "calculate_margin" for record in state.tool_records)


def test_v4_eval_report(tmp_path: Path):
    report = run_eval(Path("data/eval/v4_tasks.json"), report_path=tmp_path / "report.json")

    assert report["total"] == 2
    assert "task_success_rate" in report
    assert "avg_tool_failure_rate" in report
    assert (tmp_path / "report.json").exists()


def test_v4_llm_adapter_smoke_boundary():
    response = ModelAdapter().complete("生成一个无线耳机标题")

    assert response.provider == "deterministic"
    assert response.prompt_tokens_estimate > 0
