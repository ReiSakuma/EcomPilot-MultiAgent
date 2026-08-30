from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_RUNTIME_DIRECTORY = tempfile.TemporaryDirectory(prefix="ecompilot_recovery_eval_")
os.environ["ECOMPILOT_RUNTIME_DATA_DIR"] = _RUNTIME_DIRECTORY.name

from app.eval.recovery_eval import run_recovery_eval


def main() -> None:
    report = run_recovery_eval(
        Path("data/eval/v13_recovery_cases.json"),
        Path("reports/raw/recovery_eval.json"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["recovery_pass_rate"] != 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
