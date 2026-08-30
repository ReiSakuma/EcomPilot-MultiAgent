from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.model.contracts import StrategyCandidateProposalOutput
from app.orchestration.workflow import run_workflow
from app.tools.registry import ToolRegistry
from app.tools.strategy_candidate_tools import evaluate_strategy_candidates


GOAL = (
    "我要上架一款成本95元、售价219元、库存800件的无线耳机，"
    "主要面向游戏爱好者，毛利率不低于40%。"
)


def candidate(candidate_id: str, amount: float, objective: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "objective": objective,
        "promotion": {
            "promotion_type": "fixed_amount_coupon",
            "discount_amount_yuan": amount,
        },
        "planned_units": 300,
        "duration_days": 14,
    }


def main() -> int:
    proposal = StrategyCandidateProposalOutput.model_validate(
        {
            "candidates": [
                candidate("candidate_safe", 10, "游戏场景限量券"),
                candidate("candidate_rejected", 180, "过度优惠"),
            ]
        }
    )
    evaluation = evaluate_strategy_candidates(
        [item.model_dump(mode="json") for item in proposal.candidates],
        price=300,
        cost=95,
        min_margin_rate=0.40,
        inventory=800,
    )
    mixed_units_rejected = False
    try:
        StrategyCandidateProposalOutput.model_validate(
            {
                "candidates": [
                    {
                        **candidate("candidate_mixed", 10, "混合单位"),
                        "promotion": {
                            "promotion_type": "fixed_amount_coupon",
                            "discount_amount_yuan": 10,
                            "discount_rate": 0.10,
                        },
                    },
                    candidate("candidate_other", 5, "对照候选"),
                ]
            }
        )
    except ValidationError:
        mixed_units_rejected = True

    registry = ToolRegistry()
    schema = registry.input_model(
        "evaluate_strategy_candidates"
    ).model_json_schema()
    state = run_workflow(GOAL, approved=False)
    checks = {
        "version_is_v57": PROJECT_VERSION == "0.57.0",
        "candidate_count_is_bounded": len(proposal.candidates) == 2,
        "mixed_units_are_rejected": mixed_units_rejected,
        "evaluation_tool_is_registered": "candidates" in schema["properties"],
        "safe_candidate_survives": evaluation["eligible_candidate_ids"] == [
            "candidate_safe"
        ],
        "bad_candidate_isolated": evaluation["rejected_candidate_ids"] == [
            "candidate_rejected"
        ],
        "tool_owns_margin": evaluation["evaluations"][0]["margin_rate"] == 0.6724,
        "tool_owns_inventory": evaluation["evaluations"][0][
            "inventory_remaining"
        ]
        == 500,
        "terminal_reason_is_specific": evaluation["evaluations"][1][
            "rejection_reasons"
        ]
        == ["discount_above_policy_limit", "margin_below_minimum"],
        "legacy_workflow_still_runs": state.status in {
            "waiting_for_approval",
            "completed",
        },
        "strategy_capability_advertises_evaluator": (
            "strategy_agent"
            in registry.spec("evaluate_strategy_candidates").allowed_agents
        ),
    }
    report = {
        "version": "v57",
        "project_version": PROJECT_VERSION,
        "passed": all(checks.values()),
        "checks": checks,
        "task_id": state.task_id,
        "run_id": state.run_id,
        "candidate_evaluation": evaluation,
        "boundary": (
            "v57 lets the model propose category-specific candidates while governed local "
            "tools own arithmetic and eligibility. Deterministic narrative rendering and "
            "cross-artifact correction audit remain v58 scope."
        ),
    }
    target = PROJECT_ROOT / "reports" / "v57" / "v57_acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
