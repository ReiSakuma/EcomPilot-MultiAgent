from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A frozen evaluation must not inherit leases, fencing tokens, or checkpoints
# from an earlier local demo run.
_RUNTIME_DIRECTORY = tempfile.TemporaryDirectory(prefix="ecompilot_interview_eval_")
os.environ["ECOMPILOT_RUNTIME_DATA_DIR"] = _RUNTIME_DIRECTORY.name

from app.eval.interview_runner import run_interview_eval


def main() -> None:
    report = run_interview_eval(
        Path("data/eval/interview_eval_v1.json"),
        Path("reports/raw/interview_offline.json"),
    )
    summary = {
        key: report[key]
        for key in [
            "total",
            "passed",
            "regression_pass_rate",
            "hard_constraint_satisfaction_rate",
            "unauthorized_side_effect_count",
        ]
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["regression_pass_rate"] != 1.0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
