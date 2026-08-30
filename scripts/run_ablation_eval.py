from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.ablation_runner import run_ablation_eval


def main() -> None:
    report = run_ablation_eval(
        Path("data/eval/ablation_cases_v1.json"),
        Path("reports/raw/ablation_offline.json"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["all_expectations_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
