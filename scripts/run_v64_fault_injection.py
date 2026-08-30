from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "v64" / "fault_injection.json"

SUITES = {
    "strategy_budget_and_finalization": [
        "tests/test_v63_deterministic_finalization.py",
        "tests/test_v57_strategy_candidates.py",
    ],
    "context_compression_and_truncation": [
        "tests/test_v60_strategy_context.py",
        "tests/test_v61_evidence_planning.py",
        "tests/test_v62_react_context_budget.py",
        "tests/test_v41_semantic_compiler.py",
        "tests/test_review_optimization.py",
    ],
    "provider_and_tool_failures": [
        "tests/test_v14_real_llm_runtime.py",
        "tests/test_v12_tool_reliability.py",
        "tests/test_v36_reliability.py",
    ],
    "task_isolation_and_resume": [
        "tests/test_v38_distributed_runtime.py",
        "tests/test_v43_task_checkpoints.py",
        "tests/test_v44_batch_compiler.py",
    ],
}


def main() -> None:
    results: list[dict] = []
    for name, tests in SUITES.items():
        started = time.monotonic()
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *tests],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        results.append(
            {
                "suite": name,
                "status": "passed" if completed.returncode == 0 else "failed",
                "exit_code": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                "tests": tests,
                "summary": output[-4000:],
            }
        )
    payload = {
        "protocol_version": "v64-fault-injection-1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "passed" if all(item["status"] == "passed" for item in results) else "failed"
        ),
        "source": "deterministic_adapter_and_runtime_fault_injection",
        "notes": [
            "429, timeout, incomplete output and tool failures are injected locally.",
            "This report does not claim that the public DeepSeek service failed.",
        ],
        "suites": results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
