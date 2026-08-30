from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orchestration.workflow import run_workflow


DEMO_GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "目前库存 800 件，希望一个月内完成冷启动。请分析竞品、生成商品标题与卖点，"
    "并设计首月促销方案，要求毛利率不能低于 25%。"
)


def main() -> None:
    state = run_workflow(DEMO_GOAL, approved=True)
    print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
