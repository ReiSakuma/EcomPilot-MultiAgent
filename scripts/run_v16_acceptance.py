from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval
from app.tools.browser_tools import reset_seller_center


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)
UPSTREAM_AGENTS = (
    "market_agent",
    "listing_agent",
    "strategy_agent",
    "review_agent",
)


def main() -> None:
    reset_seller_center()
    initial = run_workflow(GOAL, approved=False)
    before = dict(initial.latest_artifacts)
    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(
            approved=True,
            approver="v16-acceptance",
            reason="verify artifact-preserving resume",
        ),
        expected_checkpoint_version=initial.checkpoint_version,
    )

    checks = {
        "waiting_run_created_five_artifacts": len(initial.artifacts) == 5,
        "all_handoffs_are_typed": all(
            handoff.artifact is not None for handoff in initial.handoffs
        ),
        "legacy_outputs_match_artifacts": all(
            initial.artifacts[artifact_id].legacy_result()
            == initial.agent_outputs[agent_name]
            for agent_name, artifact_id in initial.latest_artifacts.items()
        ),
        "parallel_agents_share_input_version": (
            initial.artifacts[before["listing_agent"]].input_state_version
            == initial.artifacts[before["strategy_agent"]].input_state_version
        ),
        "approval_resume_completed": resumed.status == "completed",
        "upstream_artifacts_preserved": all(
            resumed.latest_artifacts[name] == before[name]
            for name in UPSTREAM_AGENTS
        ),
        "browser_artifact_replaced": (
            resumed.latest_artifacts["browser_agent"] != before["browser_agent"]
        ),
        "browser_verification_passed": resumed.agent_outputs["browser_agent"]
        .get("verification", {})
        .get("verified")
        is True,
    }
    report = {
        "version": "v16",
        "passed": all(checks.values()),
        "task_id": resumed.task_id,
        "initial_run_id": initial.run_id,
        "resumed_run_id": resumed.run_id,
        "initial_state_version": initial.state_version,
        "resumed_state_version": resumed.state_version,
        "artifact_count": len(resumed.artifacts),
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
