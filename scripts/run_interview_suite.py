from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "reports" / "raw"
LOGS = RAW / "logs"
STATUS_PATH = RAW / "suite_status.json"


@dataclass
class Stage:
    name: str
    command: list[str] | None = None
    action: Callable[[], tuple[int, str]] | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the EcomPilot interview evidence suite.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Do not call an external LLM.")
    mode.add_argument(
        "--live-llm",
        action="store_true",
        help="Run 20 tasks on the configured external model. This can incur cost.",
    )
    parser.add_argument(
        "--real-browser",
        action="store_true",
        help="Run browser eval against a local server with Playwright Chromium.",
    )
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    stages = [
        Stage("preflight", action=lambda: _preflight(args.live_llm, args.real_browser)),
        Stage("unit_tests", [sys.executable, "-m", "pytest", "-q"]),
        Stage("business_regression", [sys.executable, "scripts/run_regression_eval.py"]),
        Stage("interview_eval", [sys.executable, "scripts/run_interview_eval.py"]),
        Stage("tool_eval", [sys.executable, "scripts/run_tool_reliability_eval.py"]),
        Stage("recovery_eval", [sys.executable, "scripts/run_recovery_eval.py"]),
        Stage(
            "browser_eval",
            action=_real_browser_eval if args.real_browser else None,
            command=None
            if args.real_browser
            else [sys.executable, "scripts/run_browser_eval.py"],
        ),
        Stage("ablation_eval", [sys.executable, "scripts/run_ablation_eval.py"]),
        Stage(
            "llm_comparison",
            [
                sys.executable,
                "scripts/run_llm_comparison.py",
                *(["--live-llm"] if args.live_llm else []),
            ],
        ),
        Stage("v35_mvp_gate", [sys.executable, "scripts/run_v35_mvp_gate.py"]),
    ]
    suite = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live_llm" if args.live_llm else "offline",
        "real_browser_requested": args.real_browser,
        "stages": [],
    }
    _write_status(suite)
    for stage in stages:
        started = time.perf_counter()
        if stage.action is not None:
            return_code, output = stage.action()
        else:
            completed = subprocess.run(
                stage.command,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            return_code, output = completed.returncode, completed.stdout
        log_path = LOGS / f"{stage.name}.log"
        log_path.write_text(output, encoding="utf-8")
        result = {
            "name": stage.name,
            "status": "passed" if return_code == 0 else "failed",
            "exit_code": return_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "log_path": str(log_path.relative_to(ROOT)),
        }
        suite["stages"].append(result)
        _write_status(suite)
        print(f"{stage.name:24} {result['status']:7} {result['duration_ms']:9.2f} ms")

    # The builder reads suite_status, so it runs after all evidence stages.
    report = subprocess.run(
        [sys.executable, "scripts/build_final_report.py"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    (LOGS / "final_report.log").write_text(report.stdout, encoding="utf-8")
    suite["completed_at"] = datetime.now(timezone.utc).isoformat()
    suite["status"] = (
        "passed"
        if report.returncode == 0
        and all(stage["status"] == "passed" for stage in suite["stages"])
        else "failed"
    )
    suite["final_report"] = "reports/summaries/FINAL_REPORT.md"
    _write_status(suite)
    print(f"{'final_report':24} {'passed' if report.returncode == 0 else 'failed':7}")
    print(f"suite                    {suite['status']}")
    print(f"report                   {ROOT / suite['final_report']}")
    if suite["status"] != "passed":
        raise SystemExit(1)


def _preflight(live_llm: bool, real_browser: bool) -> tuple[int, str]:
    from importlib.util import find_spec

    checks = {
        "python_supported": sys.version_info >= (3, 10),
        "interview_dataset_present": (ROOT / "data/eval/interview_eval_v1.json").exists(),
        "live_subset_present": (ROOT / "data/eval/live_llm_subset_v1.json").exists(),
        "playwright_package_installed": find_spec("playwright") is not None,
        "api_key_configured": _llm_key_configured(),
    }
    required = [checks["python_supported"], checks["interview_dataset_present"]]
    if live_llm:
        required.extend([checks["live_subset_present"], checks["api_key_configured"]])
    if real_browser:
        required.append(checks["playwright_package_installed"])
        browser_status = _browser_preflight_status()
        checks["chromium_installed"] = bool(browser_status.get("chromium_installed"))
        required.append(checks["chromium_installed"])
    output = json.dumps(checks, ensure_ascii=False, indent=2)
    return (0 if all(required) else 2), output


def _real_browser_eval() -> tuple[int, str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "ECOMPILOT_BROWSER_BACKEND": "playwright",
            "ECOMPILOT_BROWSER_BASE_URL": base_url,
            "PYTHONPATH": str(ROOT),
        }
    )
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output_parts: list[str] = []
    try:
        _wait_for_server(f"{base_url}/browser/status", process=server)
        for command in (
            [sys.executable, "scripts/run_browser_eval.py"],
            [sys.executable, "scripts/run_browser_visual_check.py"],
        ):
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            output_parts.append(completed.stdout)
            if completed.returncode != 0:
                return completed.returncode, "\n".join(output_parts)
        return 0, "\n".join(output_parts)
    except Exception as exc:
        return 1, "\n".join(output_parts + [f"{type(exc).__name__}: {exc}"])
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


def _llm_key_configured() -> bool:
    provider = os.getenv("ECOMPILOT_LLM_PROVIDER", "openai")
    provider_key = (
        os.getenv("DEEPSEEK_API_KEY")
        if provider == "deepseek"
        else os.getenv("OPENAI_API_KEY")
    )
    return bool(os.getenv("ECOMPILOT_LLM_API_KEY") or provider_key)


def _wait_for_server(url: str, *, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError(
                f"Local Seller Center exited before readiness (code={process.returncode}): "
                f"{output[-2000:]}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError(f"Local Seller Center did not become ready: {url}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _browser_preflight_status() -> dict[str, object]:
    env = os.environ.copy()
    env["ECOMPILOT_BROWSER_BACKEND"] = "playwright"
    completed = subprocess.run(
        [sys.executable, "scripts/run_browser_preflight.py"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}


def _write_status(payload: dict[str, object]) -> None:
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
