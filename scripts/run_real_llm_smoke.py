from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

provider = os.environ.setdefault("ECOMPILOT_LLM_PROVIDER", "openai")
os.environ.setdefault(
    "ECOMPILOT_LLM_MODEL",
    "deepseek-v4-flash" if provider == "deepseek" else "gpt-5-mini",
)
os.environ.setdefault(
    "ECOMPILOT_LLM_AGENTS",
    "listing_agent,strategy_agent,review_agent",
)
os.environ.setdefault("ECOMPILOT_REACT_AGENTS", "strategy_agent")
os.environ.setdefault("ECOMPILOT_LLM_FALLBACK", "fail_closed")
os.environ.setdefault("ECOMPILOT_LLM_MAX_CALLS_PER_AGENT", "2")
os.environ.setdefault("ECOMPILOT_REACT_MAX_STEPS", "2")
os.environ.setdefault("ECOMPILOT_REACT_MAX_TOOL_CALLS", "1")

from app.model.runtime import get_llm_runtime_status
from app.orchestration.workflow import run_workflow


DEMO_GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def main() -> None:
    runtime = get_llm_runtime_status()
    if not runtime["ready"]:
        print(json.dumps(runtime, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    state = run_workflow(DEMO_GOAL, approved=True)
    result = {
        "status": state.status,
        "run_id": state.run_id,
        "runtime": runtime,
        "listing": state.agent_outputs.get("listing_agent"),
        "model_records": state.model_records,
        "model_fallbacks": state.model_fallbacks,
        "tool_records": state.tool_records,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    live_calls = [
        record
        for record in state.model_records
        if record.get("status") == "completed"
        and record.get("provider") in {"openai", "openai-compatible", "deepseek"}
    ]
    if state.status != "completed" or not live_calls:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
