from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.distributed.runtime import DistributedRuntime
from app.main import _workflow_job_handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Drain the v38 durable workflow queue")
    parser.add_argument("--once", action="store_true", help="Exit when no job is available")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--worker-id", default=f"workflow-worker-{uuid4().hex[:10]}")
    args = parser.parse_args()
    runtime = DistributedRuntime()
    handlers = {"copilot_turn": _workflow_job_handler}
    while True:
        job = runtime.run_once(
            worker_id=args.worker_id,
            pool="workflow",
            handlers=handlers,
        )
        if job is not None:
            print(f"{job.job_id} {job.status} attempts={job.attempts}", flush=True)
            continue
        if args.once:
            return
        time.sleep(max(0.05, args.poll_seconds))


if __name__ == "__main__":
    main()
