from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_v47_batch_recovery.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    checks = {
        "recoverable_child_retry": result.returncode == 0,
        "three_attempt_limit": result.returncode == 0,
        "unknown_write_reconciliation_gate": result.returncode == 0,
        "retry_api_exposed": result.returncode == 0,
    }
    payload = {
        "release": "v47-batch-recovery",
        "passed": all(checks.values()),
        "checks": checks,
        "pytest": result.stdout.strip(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
