from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_v49_batch_receipt_recovery.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    checks = {
        "receipt_survives_reconnect": result.returncode == 0,
        "latest_generation_wins": result.returncode == 0,
        "completed_result_is_recoverable": result.returncode == 0,
        "tenant_isolation": result.returncode == 0,
        "browser_recovery_protocol": result.returncode == 0,
    }
    payload = {
        "release": "v49-batch-receipt-recovery",
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
