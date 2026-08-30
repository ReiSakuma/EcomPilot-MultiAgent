from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["ECOMPILOT_LLM_PROVIDER"] = "deepseek"
os.environ.setdefault("ECOMPILOT_LLM_MODEL", "deepseek-v4-pro")
os.environ.setdefault("ECOMPILOT_LLM_BASE_URL", "https://api.deepseek.com")
os.environ.setdefault(
    "ECOMPILOT_LLM_AGENTS",
    "market_agent,listing_agent,strategy_agent,review_agent,analytics_agent",
)
os.environ.setdefault(
    "ECOMPILOT_REACT_AGENTS", "market_agent,strategy_agent,analytics_agent"
)
os.environ["ECOMPILOT_LLM_FALLBACK"] = "fail_closed"
os.environ["ECOMPILOT_STRATEGY_CANDIDATES"] = "true"
os.environ.setdefault("ECOMPILOT_LLM_MAX_CALLS_PER_AGENT", "7")
os.environ.setdefault("ECOMPILOT_STRATEGY_MODEL_CALL_LIMIT", "4")
os.environ.setdefault("ECOMPILOT_REACT_INPUT_TOKEN_BUDGET", "12000")
os.environ.setdefault("ECOMPILOT_REACT_MAX_OUTPUT_TOKENS", "1600")
os.environ.setdefault("ECOMPILOT_REACT_COMPRESSION_TRIGGER_RATIO", "0.70")

from app.agents.supervisor import Supervisor  # noqa: E402
from app.eval.stability import summarize_stability_runs  # noqa: E402
from app.model.runtime import get_llm_runtime_status  # noqa: E402


NORMAL_GOAL = (
    "我要上架一款成本95元的入耳式无线耳机，目标售价199元，库存800件，"
    "最低毛利率25%，主要面向游戏爱好者。已确认功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认产品形态：入耳式。运营目标：首月冷启动，"
    "文案保持年轻、清晰、务实。"
)
SCENARIOS = {
    "normal_listing": NORMAL_GOAL,
    "evidence_plan_pressure": (
        NORMAL_GOAL
        + "请综合需求预测、历史活动、竞品价格变化和优惠模拟，但只选择真正必要的证据。"
    ),
    "long_context": NORMAL_GOAL + "补充背景：" + "保持已确认事实，不虚构参数；" * 160,
    "high_price_confirmation": NORMAL_GOAL.replace("目标售价199元", "目标售价300元"),
    "candidate_diversity": NORMAL_GOAL + "请提出适合该品类的不同促销候选，不使用固定模板。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v64 against the real DeepSeek API and emit acceptance evidence."
    )
    parser.add_argument("--profile", choices=("smoke", "full"), default="smoke")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = ROOT / "reports" / "v64" / f"live_deepseek_{args.profile}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = get_llm_runtime_status()
    if not runtime.get("ready") or runtime.get("provider") != "deepseek":
        payload = {
            "protocol_version": "v64-live-deepseek-1.0",
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

    schedule = _schedule(args.profile)
    supervisor = Supervisor()
    runs = [_run(supervisor, scenario, goal) for scenario, goal in schedule]
    stability = summarize_stability_runs(runs)
    required_gates = (
        "normal_e2e_success_at_least_95_percent",
        "strategy_model_calls_p95_at_most_4",
        "strategy_context_reduction_at_least_35_percent",
        "tool_overflow_task_failures_zero",
        "eligible_candidate_selection_failures_zero",
        "real_deepseek_records_observed",
    )
    failed_required = [
        name
        for name in required_gates
        if stability["gates"][name]["status"] != "pass"
    ]
    payload = {
        "protocol_version": "v64-live-deepseek-1.0",
        "status": "passed" if not failed_required else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": args.profile,
        "provider": runtime.get("provider"),
        "model": runtime.get("model"),
        "runtime": _safe_runtime(runtime),
        "schedule": {"total": len(schedule), "normal": sum(name == "normal_listing" for name, _ in schedule)},
        "required_gates": list(required_gates),
        "failed_required_gates": failed_required,
        "stability": stability,
        "runs": runs,
    }
    _write(output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "passed" else 1)


def _schedule(profile: str) -> list[tuple[str, str]]:
    if profile == "smoke":
        return [
            ("normal_listing", SCENARIOS["normal_listing"]),
            ("evidence_plan_pressure", SCENARIOS["evidence_plan_pressure"]),
            ("long_context", SCENARIOS["long_context"]),
        ]
    schedule = [("normal_listing", NORMAL_GOAL) for _ in range(20)]
    schedule.extend(
        (name, goal)
        for name, goal in SCENARIOS.items()
        for _ in range(10 if name in {"evidence_plan_pressure", "long_context"} else 2)
        if name != "normal_listing"
    )
    return schedule


def _run(supervisor: Supervisor, scenario: str, goal: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        state = supervisor.run(goal, approved=True, approved_by="v64_live_suite")
        return {
            "profile": "normal" if scenario == "normal_listing" else scenario,
            "scenario": scenario,
            "status": state.status,
            "outcome": state.outcome.value,
            "task_id": state.task_id,
            "run_id": state.run_id,
            "duration_seconds": round(time.monotonic() - started, 3),
            "model_call_count": len(state.model_records),
            "model_records": state.model_records,
            "model_fallbacks": state.model_fallbacks,
            "tool_call_count": len(state.tool_records),
            "tool_records": state.tool_records,
            "context_usage": state.context_usage,
            "agent_outputs": state.agent_outputs,
            "degradations": [item.model_dump(mode="json") for item in state.degradations],
            "failure": state.failure.model_dump(mode="json") if state.failure else None,
        }
    except Exception as exc:  # the report must survive one failed live case
        return {
            "profile": "normal" if scenario == "normal_listing" else scenario,
            "scenario": scenario,
            "status": "exception",
            "duration_seconds": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "model_records": [],
            "tool_records": [],
            "context_usage": {},
            "agent_outputs": {},
            "degradations": [],
        }


def _safe_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in runtime.items()
        if "key" not in key.lower() and "secret" not in key.lower()
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
