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
os.environ["ECOMPILOT_LLM_AGENTS"] = "listing_agent,strategy_agent,review_agent"
os.environ["ECOMPILOT_REACT_AGENTS"] = "strategy_agent"
os.environ["ECOMPILOT_LLM_FALLBACK"] = "fail_closed"
os.environ.setdefault("ECOMPILOT_LLM_MAX_CALLS_PER_AGENT", "2")
os.environ.setdefault("ECOMPILOT_REACT_MAX_STEPS", "2")
os.environ.setdefault("ECOMPILOT_REACT_MAX_TOOL_CALLS", "1")

from app.agents.supervisor import Supervisor  # noqa: E402
from app.model.runtime import get_llm_runtime_status  # noqa: E402


SCENARIOS = {
    "natural_form": (
        "我要上架一款成本95元的入耳式无线耳机，目标售价199元，库存800件，"
        "最低毛利率25%，主要面向游戏爱好者。已确认功能：蓝牙5.3、游戏低延迟、"
        "长续航、快充、通话降噪。运营目标：完成首月冷启动，文案清晰、务实。"
    ),
    "optional_form": (
        "我要上架一款成本95元的无线耳机，目标售价199元，库存800件，"
        "最低毛利率25%，主要面向游戏爱好者。已确认功能：蓝牙5.3、游戏低延迟、"
        "长续航、快充、通话降噪。产品形态尚未确认，文案不要声称具体形态。"
    ),
}
UNSAFE_GENERATED_PHRASES = {"性能出色", "表现出色", "卓越性能", "性能卓越", "强劲性能"}
DERIVED_EFFECT_PHRASES = {
    "连接快速稳定",
    "连接更稳",
    "功耗更低",
    "声画同步",
    "音画同步",
    "畅玩",
    "告别电量焦虑",
    "有效降低环境噪音",
    "通话清晰",
    "沟通无阻",
    "满足长时间使用",
    "充电片刻",
    "快速补充电量",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the v65 generated-content ownership checks against real DeepSeek."
    )
    parser.add_argument("--rounds", type=int, default=2, choices=range(1, 6))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = ROOT / "reports" / "v65" / "live_deepseek_selfcheck.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = get_llm_runtime_status()
    if not runtime.get("ready") or runtime.get("provider") != "deepseek":
        payload = {
            "protocol_version": "v65-live-selfcheck-1.0",
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

    supervisor = Supervisor()
    runs = [
        _run(supervisor, scenario, goal, round_number)
        for round_number in range(1, args.rounds + 1)
        for scenario, goal in SCENARIOS.items()
    ]
    failed = [item for item in runs if not item.get("passed")]
    payload = {
        "protocol_version": "v65-live-selfcheck-1.0",
        "status": "passed" if not failed else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": runtime.get("provider"),
        "model": runtime.get("model"),
        "rounds": args.rounds,
        "run_count": len(runs),
        "failed_run_ids": [item.get("run_id") for item in failed],
        "runs": runs,
    }
    _write(output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not failed else 1)


def _run(
    supervisor: Supervisor, scenario: str, goal: str, round_number: int
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        state = supervisor.run(goal, approved=False)
        listing = state.agent_outputs.get("listing_agent", {})
        review = state.agent_outputs.get("review_agent", {})
        generated_text = json.dumps(
            {
                "title": listing.get("title"),
                "keywords": listing.get("keywords", []),
                "bullets": listing.get("bullets", []),
            },
            ensure_ascii=False,
        )
        confirmed_features = list(state.constraints.get("confirmed_features") or [])
        completed_live_records = [
            item
            for item in state.model_records
            if item.get("provider") == "deepseek"
            and item.get("status") == "completed"
            and item.get("usage_source") == "actual"
        ]
        generated_terminal_findings = [
            item
            for item in review.get("review_findings", [])
            if item.get("blocking")
            and item.get("claim_origin") == "agent_generated"
            and state.status == "failed"
        ]
        checks = {
            "awaits_approval": state.status == "waiting_for_approval",
            "review_approved": review.get("approved_for_execution") is True,
            "real_deepseek_observed": bool(completed_live_records),
            "no_incomplete_model_output": not any(
                "incomplete" in str(item.get("error", "")).lower()
                for item in state.model_records
            ),
            "no_generated_claim_terminal_failure": not generated_terminal_findings,
            "unsafe_generated_phrases_removed": not any(
                phrase in generated_text
                for phrase in UNSAFE_GENERATED_PHRASES | DERIVED_EFFECT_PHRASES
            ),
            "confirmed_feature_bullets_are_grounded": _bullets_are_grounded(
                list(listing.get("bullets") or []), confirmed_features
            ),
            "semantic_audit_is_truthful": _semantic_audit_is_truthful(
                list(listing.get("semantic_corrections") or [])
            ),
            "tool_records_are_task_scoped": (
                4 <= len(state.tool_records) <= 5
                and all(
                    item.get("task_id") == state.task_id
                    for item in state.tool_records
                )
            ),
            "product_form_contract": (
                state.constraints.get("confirmed_product_form") == "入耳式"
                if scenario == "natural_form"
                else state.constraints.get("confirmed_product_form") is None
            ),
        }
        return {
            "scenario": scenario,
            "round": round_number,
            "passed": all(checks.values()),
            "checks": checks,
            "status": state.status,
            "outcome": state.outcome.value,
            "task_id": state.task_id,
            "run_id": state.run_id,
            "duration_seconds": round(time.monotonic() - started, 3),
            "model_call_count": len(state.model_records),
            "real_model_call_count": len(completed_live_records),
            "tool_call_count": len(state.tool_records),
            "listing_summary": {
                "title": listing.get("title"),
                "bullets": listing.get("bullets", []),
                "semantic_corrections": listing.get("semantic_corrections", []),
            },
            "revision_loop": (
                state.workflow_loops.get("compliance_repair").model_dump(mode="json")
                if state.workflow_loops.get("compliance_repair")
                else None
            ),
            "failure": state.failure.model_dump(mode="json") if state.failure else None,
        }
    except Exception as exc:
        return {
            "scenario": scenario,
            "round": round_number,
            "passed": False,
            "status": "exception",
            "duration_seconds": round(time.monotonic() - started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _safe_feature_statement(feature: str) -> str:
    return "支持游戏低延迟模式" if feature == "游戏低延迟" else f"支持{feature}"


def _bullets_are_grounded(bullets: list[str], confirmed_features: list[str]) -> bool:
    for bullet in bullets:
        compact = bullet.replace(" ", "").rstrip("。")
        mentioned = [
            feature
            for feature in confirmed_features
            if feature.replace(" ", "") in compact
        ]
        if not mentioned:
            continue
        expected = "，".join(_safe_feature_statement(item) for item in mentioned)
        if compact != expected.replace(" ", ""):
            return False
    return True


def _semantic_audit_is_truthful(corrections: list[dict[str, Any]]) -> bool:
    punctuation = " ，,。；;"
    for correction in corrections:
        if correction.get("issue_code") != "prohibited_marketing_claim":
            continue
        before = str(correction.get("before") or "").strip(punctuation)
        after = str(correction.get("after") or "").strip(punctuation)
        if before == after:
            return False
    return True


if __name__ == "__main__":
    main()
