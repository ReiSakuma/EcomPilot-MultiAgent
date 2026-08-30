from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "v64"

GOALS = (
    ("conversation_and_task_isolation", "对话窗口可管理多个独立任务与 Checkpoint", "tests/test_v43_task_checkpoints.py"),
    ("multi_intent_and_batch", "一句话多意图分流与多商品批任务隔离", "tests/test_v44_batch_compiler.py"),
    ("semantic_compiler", "真实模型结构化语义、错别字容错与可信字段", "tests/test_v41_semantic_compiler.py"),
    ("preflight_safety", "业务、安全和注入检查先于昂贵主流程", "tests/test_v39_preflight_stability.py"),
    ("market_statistics", "市场分层、异常样本清洗与价格门禁", "tests/test_v52_market_statistics.py tests/test_v54_market_price_gate.py"),
    ("strategy_react", "模型自主选证据，工具核算数字，候选确定性收尾", "tests/test_v57_strategy_candidates.py tests/test_v63_deterministic_finalization.py"),
    ("multi_agent_a2a", "主 Agent、专业 Agent、结构化 Handoff 与最小权限", "tests/test_v20_a2a_protocol.py tests/test_v22_capability_security.py"),
    ("memory_and_context", "长期记忆、可信摘要、上下文投影与滚动压缩", "tests/test_v33_memory_context.py tests/test_v62_react_context_budget.py"),
    ("approval_and_write", "用户确认、幂等写入、浏览器回读校验", "tests/test_v55_pricing_ui.py tests/test_v8_seller_center_execution.py"),
    ("sql_and_access", "Text-to-SQL 只读策略、租户过滤与沙盒隔离", "tests/test_v21_text_to_sql.py tests/test_v23_process_sandbox.py"),
    ("reliability_and_concurrency", "工具重试、恢复、原子性、队列与租户隔离", "tests/test_v36_reliability.py tests/test_v38_distributed_runtime.py"),
    ("observability_and_read_only_ops", "Trace、Run Bundle、只读运维与用户端计数一致", "tests/test_run_bundle_export.py tests/test_v55_pricing_ui.py"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the v64 final acceptance report.")
    parser.add_argument(
        "--reuse-regression",
        action="store_true",
        help="Do not rerun pytest; reuse an existing regression report if present.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    regression = _regression(args.reuse_regression)
    fault = _run_fault_injection()
    live = _load_live_report()
    offline_passed = regression["status"] == "passed" and fault["status"] == "passed"
    live_passed = live.get("status") == "passed"
    status = (
        "passed"
        if offline_passed and live_passed
        else "offline_passed_live_not_observed"
        if offline_passed and live.get("status") in {"not_observed", "external_blocked"}
        else "failed"
    )
    payload = {
        "protocol_version": "v64-final-acceptance-1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "claims": {
            "offline_reference_implementation_validated": offline_passed,
            "real_deepseek_validated": live_passed,
            "real_ecommerce_platform_connected": False,
            "multi_host_production_ha_claimed": False,
        },
        "goal_matrix": [
            {
                "goal": goal,
                "expected_behavior": description,
                "evidence": evidence,
                "status": "passed" if regression["status"] == "passed" else "failed",
            }
            for goal, description, evidence in GOALS
        ],
        "regression": regression,
        "fault_injection": fault,
        "live_deepseek": live,
    }
    path = REPORT_DIR / "final_acceptance.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(1 if status == "failed" else 0)


def _regression(reuse: bool) -> dict:
    path = REPORT_DIR / "regression.json"
    if reuse and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    payload = {
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "summary": output[-6000:],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def _run_fault_injection() -> dict:
    completed = subprocess.run(
        [sys.executable, "scripts/run_v64_fault_injection.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    path = REPORT_DIR / "fault_injection.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "status": "failed",
        "exit_code": completed.returncode,
        "summary": (completed.stdout + completed.stderr)[-4000:],
    }


def _load_live_report() -> dict:
    for name in ("live_deepseek_full.json", "live_deepseek_smoke.json"):
        path = REPORT_DIR / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {
        "status": "not_observed",
        "reason": "Run scripts/run_v64_live_deepseek_suite.py with a real API key.",
    }


if __name__ == "__main__":
    main()
