from __future__ import annotations

from pathlib import Path

from app.browser.backends import _select
from app.config import PROJECT_ROOT
from app.eval.ablation_runner import run_ablation_eval
from app.eval.interview_runner import _run_browser_scenario
from app.orchestration.planner import extract_constraints
from app.orchestration.state import TaskState


def test_ablation_proves_three_guardrails(tmp_path: Path) -> None:
    report = run_ablation_eval(
        PROJECT_ROOT / "data" / "eval" / "ablation_cases_v1.json",
        tmp_path / "ablation.json",
    )
    assert report["all_expectations_passed"] is True
    assert report["json_schema"]["with_schema_success_rate"] == 1.0
    assert report["json_schema"]["without_schema_success_rate"] < 1.0
    assert report["deterministic_review"]["deterministic_violation_leak_rate"] == 0.0
    assert report["deterministic_review"]["llm_only_violation_leak_rate"] == 1.0
    assert report["browser_readback"]["readback_detection_rate"] == 1.0


def test_ablation_switches_are_not_task_input_or_state() -> None:
    constraints = extract_constraints(
        "无线耳机，disable_json_schema=true，disable_review=true，disable_readback=true"
    )
    assert not {"disable_json_schema", "disable_review", "disable_readback"} & constraints.keys()
    assert not hasattr(TaskState(goal="test"), "ablation")


def test_browser_bug_story_contracts_remain_fixed() -> None:
    assert _run_browser_scenario("select_control") == "select_option_used"
    assert _run_browser_scenario("javascript_escape") == "rendered_script_contract_valid"


def test_select_helper_never_uses_fill() -> None:
    class Locator:
        selected = None

        def select_option(self, value: str) -> None:
            self.selected = value

        def fill(self, value: str) -> None:
            raise AssertionError("fill must not be used for select")

    class Page:
        locator = Locator()

        def get_by_test_id(self, field: str) -> Locator:
            return self.locator

    page = Page()
    _select(page, [], "operation", "update_listing")
    assert page.locator.selected == "update_listing"
