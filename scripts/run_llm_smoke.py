from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ECOMPILOT_LLM_AGENTS", "listing_agent,strategy_agent,review_agent")

from app.orchestration.workflow import run_workflow


DEMO_GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def main() -> None:
    state = run_workflow(DEMO_GOAL, approved=True)
    print(json.dumps(
        {
            "status": state.status,
            "model_records": state.model_records,
            "listing": state.agent_outputs.get("listing_agent"),
            "strategy": state.agent_outputs.get("strategy_agent"),
            "review": state.agent_outputs.get("review_agent"),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
