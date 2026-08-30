from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.orchestration.recovery import RecoveryManager, RecoveryNotAllowedError  # noqa: E402
from app.orchestration.state import TaskState  # noqa: E402
from app.reliability.classifier import classify_failure  # noqa: E402
from app.reliability.dead_letter import DeadLetterStore  # noqa: E402
from app.reliability.models import DeadLetterRecord, FailureTaxonomy, RetryBudget  # noqa: E402
from app.tools.contracts import EmptyInput  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402
from app.tools.schemas import ToolSpec, TransientToolError, UnknownWriteStateError  # noqa: E402


def main() -> None:
    scenarios = {
        "timeout": classify_failure(TimeoutError("timeout")) is FailureTaxonomy.transient,
        "rate_limit_429": classify_failure(RuntimeError("HTTP 429 rate limit"))
        is FailureTaxonomy.rate_limit,
        "gateway_502": classify_failure(RuntimeError("HTTP 502 bad gateway"))
        is FailureTaxonomy.transient,
        "schema_invalid": classify_failure(RuntimeError("invalid JSON schema"))
        is FailureTaxonomy.schema_invalid,
        "worker_crash": classify_failure(RuntimeError("worker crashed unexpectedly"))
        is FailureTaxonomy.unknown,
    }

    registry = ToolRegistry()
    calls = 0

    def flaky() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TransientToolError("HTTP 502 temporary")
        return {"ok": True}

    registry.register(
        ToolSpec(name="fault_injection_read", allowed_agents={"acceptance"}, max_retries=2),
        flaky,
        EmptyInput,
        lambda result: None,
    )
    registry.bind_retry_budget("task_acceptance", RetryBudget(max_attempts=2))
    with registry.agent_scope("acceptance", task_id="task_acceptance"):
        registry.call("fault_injection_read")
    scenarios["bounded_retry"] = calls == 3 and registry.retry_budget("task_acceptance").consumed == 2

    write_registry = ToolRegistry()
    write_calls = 0

    def unknown_write() -> None:
        nonlocal write_calls
        write_calls += 1
        raise TimeoutError("write confirmation timeout")

    write_registry.register(
        ToolSpec(
            name="fault_injection_write",
            side_effect=True,
            operation_type="write",
            idempotency="keyed",
            reconcile_tool="authoritative_readback",
            compensation="manual",
            allowed_agents={"acceptance"},
            max_retries=3,
        ),
        unknown_write,
        EmptyInput,
        lambda result: None,
    )
    try:
        with write_registry.agent_scope("acceptance", task_id="task_write"):
            write_registry.call("fault_injection_write")
    except UnknownWriteStateError:
        pass
    scenarios["unknown_write_no_duplicate"] = (
        write_calls == 1 and write_registry.records()[-1].status == "unknown"
    )

    state = TaskState(goal="fault recovery")
    state.status = "needs_attention"
    state.tool_records = [
        {"status": "unknown", "side_effect": True, "input_hash": "write-hash"}
    ]
    try:
        RecoveryManager().prepare(state, "run_after_crash", retry_node="market")
    except RecoveryNotAllowedError:
        scenarios["recovery_requires_readback"] = True
    else:
        scenarios["recovery_requires_readback"] = False

    with tempfile.TemporaryDirectory(prefix="ecompilot-v36-dlq-") as directory:
        store = DeadLetterStore(Path(directory) / "dlq.db")
        record = DeadLetterRecord(
            task_id="task_attention",
            tenant_id="tenant_demo",
            stage="worker",
            category=FailureTaxonomy.unknown,
            error_signature="worker-crash",
            user_message="任务需要人工处理",
            developer_message="worker crashed",
        )
        store.enqueue(record)
        store.enqueue(record.model_copy(update={"record_id": "duplicate"}))
        scenarios["dlq_idempotent"] = len(store.list(tenant_id="tenant_demo")) == 1
        scenarios["dlq_tenant_isolated"] = store.list(tenant_id="tenant_other") == []

    report = {
        "version": "v36-recoverable-execution",
        "passed": all(scenarios.values()),
        "scenarios": scenarios,
        "faults": ["timeout", "429", "502", "schema_invalid", "worker_crash"],
        "duplicate_writes": 0 if scenarios["unknown_write_no_duplicate"] else 1,
        "boundary": "Local single-service fault injection; distributed fencing is planned for V38.",
    }
    output = ROOT / "reports" / "raw" / "v36_reliability_acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
