from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.browser_eval import run_browser_eval
from app.browser.runtime import get_browser_runtime_status


def main() -> None:
    profile = "playwright" if get_browser_runtime_status()["real_browser_enabled"] else "mock"
    report = run_browser_eval(
        Path("data/eval/v15_browser_cases.json"),
        Path(f"reports/raw/browser_{profile}.json"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["pass_rate"] < 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
