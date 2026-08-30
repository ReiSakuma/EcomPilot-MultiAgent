from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TRACE_DIR
from app.sandbox.runner import SandboxTimeoutError, SqlSandboxRunner
from app.sql.database import MarketDatabase, SqlExecutionError
from app.sql.policy import SqlPolicyDecision, SqlPolicyGateway
from app.sql.service import MarketSqlService
from scripts.run_v21_acceptance import run_fixture_task


def main() -> None:
    os.environ["V23_PARENT_ONLY_SECRET"] = "must-not-reach-sandbox"
    state = run_fixture_task()
    research = state.agent_outputs["market_agent"]["sql_research"]
    receipt = research["sandbox"]
    sql_tool = next(
        record
        for record in state.tool_records
        if record["tool_name"] == "query_market_database"
    )
    trace_text = (TRACE_DIR / f"{state.run_id}.jsonl").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="ecompilot-v23-") as directory:
        root = Path(directory)
        database = MarketDatabase(root / "market.db")
        forged = SqlPolicyDecision(
            status="allowed",
            query_hash="0" * 64,
            normalized_sql="UPDATE products SET price = 1",
            tables=("products",),
            columns=("products.price",),
            enforced_limit=10,
        )
        try:
            database.execute(forged)
        except SqlExecutionError as exc:
            forged_write_denied = "not authorized" in str(exc)
        else:
            forged_write_denied = False

        slow_worker = root / "slow_worker.py"
        slow_worker.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
        placeholder = root / "placeholder.db"
        placeholder.touch()
        runner = SqlSandboxRunner(timeout_seconds=0.1, worker_path=slow_worker)
        try:
            runner.execute(
                SqlPolicyGateway().authorize(
                    "SELECT COUNT(*) AS count FROM products"
                ),
                database_path=placeholder,
                dataset_version="acceptance",
            )
        except SandboxTimeoutError as exc:
            timeout_killed = (
                exc.receipt is not None
                and exc.receipt.status == "timed_out"
                and exc.receipt.exit_code == -signal.SIGKILL
            )
        else:
            timeout_killed = False

        policy_service = MarketSqlService(root / "policy.db")
        try:
            policy_service.query("DELETE FROM products")
        except Exception:
            denied_audit = policy_service.audits()[0]
            policy_blocked_before_worker = (
                denied_audit["status"] == "denied"
                and denied_audit["sandbox"] is None
            )
        else:
            policy_blocked_before_worker = False

    isolation = receipt["isolation"]
    checks = {
        "sql_executed_in_separate_process": receipt["worker_pid"] != os.getpid(),
        "isolated_python_without_site_packages": isolation["isolated_python"]
        and isolation["site_packages_disabled"],
        "shell_was_never_enabled": isolation["shell_enabled"] is False,
        "parent_secrets_were_scrubbed": isolation["secret_environment_present"] is False
        and "V23_PARENT_ONLY_SECRET" not in isolation["environment_allowlist"],
        "temporary_working_directory_was_reclaimed": isolation[
            "working_directory_removed"
        ],
        "os_resource_limits_were_applied": all(
            name in isolation["resource_limits_applied"]
            for name in ("cpu", "address_space", "open_files", "file_size")
        ),
        "sqlite_remained_read_only_with_authorizer": isolation["sqlite_read_only"]
        and isolation["sqlite_authorizer"],
        "forged_write_was_denied_inside_worker": forged_write_denied,
        "stuck_worker_was_killed_by_parent": timeout_killed,
        "unsafe_sql_was_blocked_before_spawn": policy_blocked_before_worker,
        "artifact_tool_and_trace_share_receipt": sql_tool["result_summary"]["sandbox"][
            "sandbox_id"
        ]
        == receipt["sandbox_id"]
        and receipt["sandbox_id"] in trace_text,
        "capability_binding_remained_active": sql_tool["capability_id"]
        == "market.research"
        and bool(sql_tool["capability_token_id"]),
    }
    report = {
        "version": "v23",
        "passed": all(checks.values()),
        "task_id": state.task_id,
        "run_id": state.run_id,
        "sandbox_id": receipt["sandbox_id"],
        "checks": checks,
        "boundary": (
            "The SQL worker is a real isolated subprocess with scrubbed environment and OS "
            "resource limits. It is not a container, namespace, seccomp, VM, or arbitrary-code sandbox."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
