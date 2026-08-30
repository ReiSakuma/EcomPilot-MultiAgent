from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.profile_runner import run_profile_eval


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    report = run_profile_eval(
        Path("data/eval/live_llm_subset_v1.json"),
        args.report,
        profile=args.profile,
        require_live=args.require_live,
    )
    print(
        json.dumps(
            {
                "profile": args.profile,
                "total_cases": report["total_cases"],
                "task_success_rate": report["task_success_rate"],
                "model_call_count": report["model_call_count"],
                "cost_usd_estimate": report["cost_usd_estimate"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
