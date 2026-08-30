from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval.comparison import build_llm_comparison


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic baseline and optional paid live-LLM profile."
    )
    parser.add_argument("--live-llm", action="store_true")
    parser.add_argument(
        "--provider",
        choices=["openai", "openai-compatible", "deepseek"],
        default=os.getenv("ECOMPILOT_LLM_PROVIDER", "openai"),
    )
    parser.add_argument("--model", default=os.getenv("ECOMPILOT_LLM_MODEL"))
    parser.add_argument("--base-url", default=os.getenv("ECOMPILOT_LLM_BASE_URL"))
    args = parser.parse_args()
    model = args.model or (
        "deepseek-v4-flash" if args.provider == "deepseek" else "gpt-5-mini"
    )
    reports = Path("reports/raw")
    reports.mkdir(parents=True, exist_ok=True)
    baseline_path = reports / "profile_deterministic.json"
    live_path = reports / "profile_live_llm.json"

    baseline_env = os.environ.copy()
    baseline_env.update(
        {
            "ECOMPILOT_LLM_PROVIDER": "deterministic",
            "ECOMPILOT_LLM_MODEL": "local-rule-v6",
            "ECOMPILOT_LLM_AGENTS": "",
            "ECOMPILOT_LLM_FALLBACK": "fail_closed",
        }
    )
    _run_profile("deterministic_baseline", baseline_path, baseline_env, require_live=False)

    completed_live_path: Path | None = None
    if args.live_llm:
        provider_key = (
            os.getenv("DEEPSEEK_API_KEY")
            if args.provider == "deepseek"
            else os.getenv("OPENAI_API_KEY")
        )
        if not (os.getenv("ECOMPILOT_LLM_API_KEY") or provider_key):
            raise SystemExit(
                "Live LLM requested but no API key is configured. "
                "Set ECOMPILOT_LLM_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY."
            )
        live_env = os.environ.copy()
        live_env.update(
            {
                "ECOMPILOT_LLM_PROVIDER": args.provider,
                "ECOMPILOT_LLM_MODEL": model,
                "ECOMPILOT_LLM_AGENTS": (
                    "market_agent,listing_agent,strategy_agent,review_agent"
                ),
                "ECOMPILOT_LLM_FALLBACK": "fail_closed",
            }
        )
        if args.provider == "deepseek":
            live_env["ECOMPILOT_REACT_AGENTS"] = "market_agent"
            live_env["ECOMPILOT_LLM_MAX_CALLS_PER_AGENT"] = "3"
            live_env["ECOMPILOT_REACT_MAX_STEPS"] = "3"
        if args.base_url:
            live_env["ECOMPILOT_LLM_BASE_URL"] = args.base_url
        elif args.provider == "deepseek":
            live_env["ECOMPILOT_LLM_BASE_URL"] = "https://api.deepseek.com"
        _run_profile(
            f"real_llm_{args.provider}_full_guardrails",
            live_path,
            live_env,
            require_live=True,
        )
        completed_live_path = live_path

    comparison = build_llm_comparison(
        baseline_path,
        completed_live_path,
        reports / "llm_comparison.json",
        Path("reports/summaries/listing_blind_review.csv"),
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


def _run_profile(
    profile: str, report: Path, env: dict[str, str], *, require_live: bool
) -> None:
    command = [
        sys.executable,
        "scripts/run_profile_eval.py",
        "--profile",
        profile,
        "--report",
        str(report),
    ]
    if require_live:
        command.append("--require-live")
    subprocess.run(command, env=env, check=True)


if __name__ == "__main__":
    main()
