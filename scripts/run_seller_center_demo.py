from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orchestration.workflow import run_workflow
from app.tools.browser_tools import get_seller_center_snapshot, reset_seller_center


DEMO_GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def main() -> None:
    reset_seller_center()
    state = run_workflow(DEMO_GOAL, approved=True)
    print(json.dumps(
        {
            "task_status": state.status,
            "browser_agent": state.agent_outputs.get("browser_agent"),
            "seller_center": get_seller_center_snapshot(),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
