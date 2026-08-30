from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.runner import run_eval


def main() -> None:
    dataset = Path("data/eval/v9_regression_tasks.json")
    report_path = Path("reports/raw/business_regression.json")
    report = run_eval(dataset, report_path=report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["regression_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
