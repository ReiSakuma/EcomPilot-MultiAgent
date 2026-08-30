from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_v48_batch_dispatch.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    checks = {
        "durable_browser_queue": result.returncode == 0,
        "logical_idempotency": result.returncode == 0,
        "worker_retry_and_dead_state": result.returncode == 0,
        "tenant_scoped_status": result.returncode == 0,
        "dispatch_status_api": result.returncode == 0,
    }
    payload = {
        "release": "v48-durable-batch-runtime",
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
