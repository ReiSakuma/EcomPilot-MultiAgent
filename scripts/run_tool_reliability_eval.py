from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.tool_reliability import run_tool_reliability_eval


def main() -> None:
    report = run_tool_reliability_eval(
        Path("data/eval/v12_tool_reliability_cases.json"),
        Path("reports/raw/tool_reliability.json"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["pass_rate"] != 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
