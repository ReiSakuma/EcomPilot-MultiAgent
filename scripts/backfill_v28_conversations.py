from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CHECKPOINT_DIR, CONVERSATION_DATABASE_PATH
from app.conversations.repository import ConversationRepository
from app.orchestration.checkpoint import CheckpointError, CheckpointStore


def backfill(checkpoint_dir: Path, database_path: Path) -> dict[str, object]:
    checkpoint_store = CheckpointStore(checkpoint_dir)
    repository = ConversationRepository(database_path)
    report: dict[str, object] = {
        "checkpoint_dir": str(checkpoint_dir),
        "database_path": str(database_path),
        "indexed": 0,
        "skipped": 0,
        "errors": [],
    }
    for checkpoint_path in sorted(checkpoint_dir.glob("task_*.json")):
        task_id = checkpoint_path.stem
        try:
            state = checkpoint_store.load(task_id)
            if state.conversation_id and state.turn_id:
                report["skipped"] = int(report["skipped"]) + 1
                continue
            repository.backfill_task(state)
            checkpoint_store.save(state)
            report["indexed"] = int(report["indexed"]) + 1
        except CheckpointError as exc:
            report["errors"].append({"task_id": task_id, "error": str(exc)})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill V26/V27 task checkpoints into V28 conversations")
    parser.add_argument("--checkpoint-dir", type=Path, default=CHECKPOINT_DIR)
    parser.add_argument("--database", type=Path, default=CONVERSATION_DATABASE_PATH)
    arguments = parser.parse_args()
    result = backfill(arguments.checkpoint_dir, arguments.database)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["errors"] else 0)


if __name__ == "__main__":
    main()
