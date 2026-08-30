from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.observability.store import TraceStore
from app.orchestration.workflow import run_workflow


def main() -> None:
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
        "库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )
    trace = TraceStore().get_run(state.run_id)
    print(
        json.dumps(
            {
                "run_id": state.run_id,
                "summary": trace["summary"],
                "event_types": [event.get("event_type") for event in trace["events"]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
