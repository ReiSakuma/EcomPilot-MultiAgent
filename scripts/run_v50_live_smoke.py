from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "v50" / "live_deepseek_smoke.json"
SECRET_FIELDS = {"api_key", "authorization", "access_token", "secret"}


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ECOMPILOT_LLM_PROVIDER"] = "deepseek"
    env.setdefault("ECOMPILOT_LLM_MODEL", "deepseek-v4-pro")
    env.setdefault("ECOMPILOT_LLM_BASE_URL", "https://api.deepseek.com")
    key = env.get("ECOMPILOT_LLM_API_KEY") or env.get("DEEPSEEK_API_KEY")
    if not key:
        payload = {
            "status": "not_run",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": "deepseek",
            "model": env["ECOMPILOT_LLM_MODEL"],
            "error": "DEEPSEEK_API_KEY or ECOMPILOT_LLM_API_KEY is not configured",
        }
        _write(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    completed = subprocess.run(
        [sys.executable, "scripts/run_real_llm_smoke.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "failed",
            "provider": "deepseek",
            "model": env["ECOMPILOT_LLM_MODEL"],
            "error": (completed.stderr or completed.stdout or "No JSON output")[-2000:],
        }
    payload = _redact(payload)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["smoke_exit_code"] = completed.returncode
    _write(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if completed.returncode != 0:
        raise SystemExit(1)


def _redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SECRET_FIELDS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _write(payload: dict[str, object]) -> None:
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
