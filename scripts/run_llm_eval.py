from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.llm_eval import run_llm_eval


def main() -> None:
    report = run_llm_eval(Path("data/eval/v14_llm_tasks.json"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["case_structured_success_rate"] < 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
