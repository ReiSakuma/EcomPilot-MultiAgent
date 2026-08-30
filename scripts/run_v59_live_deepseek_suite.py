from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["ECOMPILOT_LLM_PROVIDER"] = "deepseek"
os.environ.setdefault("ECOMPILOT_LLM_MODEL", "deepseek-v4-pro")
os.environ.setdefault("ECOMPILOT_LLM_BASE_URL", "https://api.deepseek.com")
os.environ.setdefault(
    "ECOMPILOT_LLM_AGENTS",
    "market_agent,listing_agent,strategy_agent,review_agent,analytics_agent",
)
os.environ.setdefault("ECOMPILOT_REACT_AGENTS", "market_agent,strategy_agent,analytics_agent")
os.environ["ECOMPILOT_LLM_FALLBACK"] = "fail_closed"
os.environ["ECOMPILOT_STRATEGY_CANDIDATES"] = "true"
os.environ.setdefault("ECOMPILOT_LLM_MAX_CALLS_PER_AGENT", "7")

from app.agents.supervisor import Supervisor
from app.model.runtime import get_llm_runtime_status
from app.release.v59 import validate_live_deepseek_report


SCENARIOS = (
    ("normal_price", "成本95元的入耳式无线耳机，售价199元，库存800件，最低毛利率25%，面向游戏爱好者，已确认蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"),
    ("high_price", "成本95元的入耳式无线耳机，售价300元，库存800件，最低毛利率25%，面向游戏爱好者，已确认蓝牙5.3和游戏低延迟。"),
    ("price_recovery", "__RESUME_HIGH_PRICE_TASK__"),
    ("high_price_with_evidence", "高端品牌入耳式无线耳机，含两年换新和独家赛事联名，成本95元，售价300元，库存800件，最低毛利率25%，已确认蓝牙5.3和游戏低延迟。"),
    ("dynamic_candidates", "为售价199元、成本95元、库存800件的入耳式游戏耳机制定首发策略，最低毛利率25%，已确认蓝牙5.3和游戏低延迟，不要使用固定模板。"),
    ("single_candidate_failure", "售价199元、成本95元的入耳式游戏耳机，库存800件。请比较金额券和赠品方案；赠品成本未知，最低毛利率25%，已确认蓝牙5.3和游戏低延迟。"),
    ("all_candidates_failure", "售价199元、成本95元的入耳式游戏耳机，库存800件，最低毛利率50%。首发策略必须使用至少20元优惠券，所有候选都必须满足毛利底线。已确认蓝牙5.3和游戏低延迟。"),
)


def main() -> None:
    output = ROOT / "reports" / "v59" / "live_deepseek_suite.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = get_llm_runtime_status()
    if not runtime["ready"] or runtime["provider"] != "deepseek":
        payload = {
            "status": "external_blocked",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": runtime.get("provider"),
            "model": runtime.get("model"),
            "error": ",".join(runtime.get("issues") or ["deepseek_runtime_not_ready"]),
            "runs": [],
        }
        _write(output, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    runs: list[dict] = []
    supervisor = Supervisor()
    scenario_states = {}
    for name, goal in SCENARIOS:
        started = time.monotonic()
        if name == "price_recovery":
            high_price = scenario_states["high_price"]
            state = supervisor.resume(
                high_price.task_id,
                constraint_updates={"target_price": 199.0},
                expected_checkpoint_version=high_price.checkpoint_version,
                requested_by="v59_live_suite",
                reason="用户采用市场建议价格后继续原任务",
            )
        else:
            state = supervisor.run(goal, approved=True, approved_by="v59_live_suite")
        scenario_states[name] = state
        runs.append(
            {
                "scenario": name,
                "status": state.status,
                "outcome": state.outcome.value,
                "task_id": state.task_id,
                "run_id": state.run_id,
                "checkpoint_version": state.checkpoint_version,
                "duration_seconds": round(time.monotonic() - started, 3),
                "model_call_count": len(state.model_records),
                "model_records": state.model_records,
                "model_fallbacks": state.model_fallbacks,
                "tool_call_count": len(state.tool_records),
                "tool_records": state.tool_records,
                "degradations": [item.model_dump(mode="json") for item in state.degradations],
            }
        )
    validation = validate_live_deepseek_report({"runs": runs})
    payload = {
        "status": "completed" if validation["valid"] else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": runtime["provider"],
        "model": runtime["model"],
        "runtime": runtime,
        "validation": validation,
        "runs": runs,
    }
    _write(output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not validation["valid"]:
        raise SystemExit(1)


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
