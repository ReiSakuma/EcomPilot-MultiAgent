from __future__ import annotations

import json
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.config import TRACE_DIR
from app.demo_ui import DEMO_HTML
from app.main import sandbox_status
from app.sandbox.runner import (
    SANDBOX_ENVIRONMENT,
    SandboxProtocolError,
    SandboxTimeoutError,
    SqlSandboxRunner,
)
from app.sql.database import MarketDatabase, SqlExecutionError
from app.sql.policy import SqlPolicyDecision, SqlPolicyDeniedError, SqlPolicyGateway
from app.sql.service import MarketSqlService
from scripts.run_v21_acceptance import run_fixture_task


def allowed_decision() -> SqlPolicyDecision:
    return SqlPolicyGateway().authorize("SELECT COUNT(*) AS count FROM products")


def test_sql_runs_in_a_different_process_with_os_limits(tmp_path: Path) -> None:
    service = MarketSqlService(tmp_path / "market.db")

    result = service.query("SELECT COUNT(*) AS count FROM products")
    receipt = result["sandbox"]
    isolation = receipt["isolation"]

    assert result["rows"] == [{"count": 200}]
    assert receipt["worker_pid"] != os.getpid()
    assert receipt["exit_code"] == 0
    assert receipt["status"] == "completed"
    assert isolation["separate_process"] is True
    assert isolation["isolated_python"] is True
    assert isolation["site_packages_disabled"] is True
    assert isolation["shell_enabled"] is False
    assert isolation["working_directory_removed"] is True
    assert {"cpu", "address_space", "open_files", "file_size"}.issubset(
        isolation["resource_limits_applied"]
    )


def test_worker_environment_is_allowlisted_and_does_not_receive_api_keys(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-worker")
    monkeypatch.setenv("ECOMPILOT_LLM_API_KEY", "must-not-reach-worker-either")

    receipt = MarketSqlService(tmp_path / "market.db").query(
        "SELECT AVG(price) AS avg_price FROM products"
    )["sandbox"]
    isolation = receipt["isolation"]

    assert isolation["environment_allowlist"] == sorted(SANDBOX_ENVIRONMENT)
    assert isolation["environment_key_count"] == len(SANDBOX_ENVIRONMENT)
    assert isolation["secret_environment_present"] is False
    assert "must-not-reach-worker" not in json.dumps(receipt)


def test_policy_denial_happens_before_the_sandbox_is_started(tmp_path: Path) -> None:
    class MustNotRun:
        called = False

        def execute(self, *_args, **_kwargs):
            self.called = True
            raise AssertionError("sandbox should not run")

        def status(self):
            return {"backend": "test"}

    runner = MustNotRun()
    service = MarketSqlService(tmp_path / "market.db", sandbox_runner=runner)

    with pytest.raises(SqlPolicyDeniedError):
        service.query("DELETE FROM products")

    assert runner.called is False
    assert service.audits()[0]["status"] == "denied"
    assert service.audits()[0]["sandbox"] is None


def test_worker_authorizer_rejects_forged_write_decision(tmp_path: Path) -> None:
    database = MarketDatabase(tmp_path / "market.db")
    forged = SqlPolicyDecision(
        status="allowed",
        query_hash="0" * 64,
        normalized_sql="UPDATE products SET price = 1",
        tables=("products",),
        columns=("products.price",),
        enforced_limit=10,
    )

    with pytest.raises(SqlExecutionError, match="not authorized") as captured:
        database.execute(forged)

    receipt = captured.value.receipt
    assert receipt["status"] == "failed"
    assert receipt["worker_pid"] != os.getpid()
    assert database.sandbox_status()["isolation"]["sqlite_authorizer"] is True


def test_parent_kills_worker_that_exceeds_wall_time(tmp_path: Path) -> None:
    worker = tmp_path / "slow_worker.py"
    worker.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    database = tmp_path / "market.db"
    database.touch()
    runner = SqlSandboxRunner(timeout_seconds=0.1, worker_path=worker)

    with pytest.raises(SandboxTimeoutError) as captured:
        runner.execute(
            allowed_decision(),
            database_path=database,
            dataset_version="test",
        )

    receipt = captured.value.receipt
    assert receipt.status == "timed_out"
    assert receipt.exit_code == -signal.SIGKILL
    assert receipt.duration_ms < 1500


def test_malformed_worker_response_is_rejected(tmp_path: Path) -> None:
    worker = tmp_path / "malformed_worker.py"
    worker.write_text("print('not-json')\n", encoding="utf-8")
    database = tmp_path / "market.db"
    database.touch()
    runner = SqlSandboxRunner(worker_path=worker)

    with pytest.raises(SandboxProtocolError, match="malformed JSON") as captured:
        runner.execute(
            allowed_decision(), database_path=database, dataset_version="test"
        )

    assert captured.value.receipt.status == "failed"


def test_oversized_worker_response_is_rejected(tmp_path: Path) -> None:
    worker = tmp_path / "oversized_worker.py"
    worker.write_text("import sys\nsys.stdout.write('x' * 50000)\n", encoding="utf-8")
    database = tmp_path / "market.db"
    database.touch()
    runner = SqlSandboxRunner(worker_path=worker, max_output_bytes=16_384)

    with pytest.raises(SandboxProtocolError, match="output limit") as captured:
        runner.execute(
            allowed_decision(), database_path=database, dataset_version="test"
        )

    assert captured.value.receipt.response_bytes == 50_000


def test_parallel_queries_get_independent_sandbox_processes(tmp_path: Path) -> None:
    service = MarketSqlService(tmp_path / "market.db")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: service.query("SELECT COUNT(*) AS count FROM products"),
                range(4),
            )
        )

    sandbox_ids = {result["sandbox"]["sandbox_id"] for result in results}
    worker_pids = {result["sandbox"]["worker_pid"] for result in results}
    assert len(sandbox_ids) == 4
    assert len(worker_pids) == 4
    assert all(result["rows"] == [{"count": 200}] for result in results)


def test_react_sql_artifact_tool_record_and_trace_share_sandbox_receipt() -> None:
    state = run_fixture_task()
    research = state.agent_outputs["market_agent"]["sql_research"]
    sql_tool = next(
        record
        for record in state.tool_records
        if record["tool_name"] == "query_market_database"
    )
    trace_text = (TRACE_DIR / f"{state.run_id}.jsonl").read_text(encoding="utf-8")

    assert research["sandbox"]["status"] == "completed"
    assert research["policy"]["process_isolated"] is True
    assert sql_tool["result_summary"]["sandbox"]["sandbox_id"] == research["sandbox"][
        "sandbox_id"
    ]
    assert research["sandbox"]["sandbox_id"] in trace_text
    assert '"separate_process":true' in trace_text


def test_sql_audit_contains_sandbox_receipt_without_result_rows(tmp_path: Path) -> None:
    service = MarketSqlService(tmp_path / "market.db")
    service.query("SELECT name FROM products LIMIT 2")

    audit = service.audits()[0]

    assert audit["sandbox"]["status"] == "completed"
    assert audit["sandbox"]["isolation"]["secret_environment_present"] is False
    assert "rows" not in audit


def test_sandbox_status_api_and_operations_ui_show_honest_boundaries() -> None:
    status = sandbox_status()

    assert status["backend"] == "subprocess"
    assert status["command_mode"] == "fixed_argv_no_shell"
    assert status["worker_exists"] is True
    assert status["isolation"]["separate_process"] is True
    assert status["isolation"]["namespaces"] is False
    assert status["isolation"]["seccomp"] is False
    assert status["isolation"]["container"] is False
    assert "Sandbox" in DEMO_HTML
    assert "进程隔离" in DEMO_HTML
    assert "/api/sandbox/status" in DEMO_HTML
