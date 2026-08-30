from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.distributed.runtime import DistributedRuntime


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish v38 Transactional Outbox events")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--publisher-id", default=f"outbox-relay-{uuid4().hex[:10]}")
    args = parser.parse_args()
    runtime = DistributedRuntime()
    while True:
        event = runtime.lease_outbox(publisher_id=args.publisher_id)
        if event is None:
            if args.once:
                return
            time.sleep(max(0.05, args.poll_seconds))
            continue
        # The interview sink is stdout; Kafka/SQS can replace it without changing leases.
        print(
            json.dumps(
                {
                    "outbox_id": event["outbox_id"],
                    "event_type": event["event_type"],
                    "aggregate_id": event["aggregate_id"],
                    "payload": event["payload"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        runtime.mark_outbox_published(
            outbox_id=event["outbox_id"],
            publisher_id=args.publisher_id,
            lease_token=event["lease_token"],
        )


if __name__ == "__main__":
    main()
