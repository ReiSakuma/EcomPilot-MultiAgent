from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.runner import run_eval


def main() -> None:
    dataset = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/eval/v0_tasks.json")
    report = run_eval(dataset)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
