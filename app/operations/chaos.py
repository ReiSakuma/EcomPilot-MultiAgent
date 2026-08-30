from __future__ import annotations

import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from app.conversations.repository import ConversationRepository
from app.distributed.runtime import DistributedRuntime
from app.memory.conversation import ConversationMemoryService
from app.operations.models import ChaosReport, ChaosScenario


def _retry_scenario(
    runtime: DistributedRuntime,
    *,
    fault: str,
    pool: str,
    handler: Callable[[dict[str, Any]], dict[str, Any]],
    max_attempts: int = 3,
) -> tuple[int, dict[str, Any] | None, str]:
    job, _ = runtime.enqueue(
        tenant_id="tenant_chaos", pool=pool, job_type=fault,
        idempotency_key=f"chaos:{fault}", payload={"fault": fault},
        max_attempts=max_attempts, enforce_rate_limit=False,
    )
    current = job
    for index in range(max_attempts):
        current = runtime.run_once(
            worker_id=f"chaos-{fault}-{index}", pool=pool, handlers={fault: handler}
        ) or current
        if current.status in {"completed", "dead"}:
            break
    return current.attempts, current.result, current.status


def run_chaos_experiments() -> ChaosReport:
    """Run deterministic, bounded failure injection against real local protocols."""

    scenarios: list[ChaosScenario] = []
    with tempfile.TemporaryDirectory(prefix="ecompilot-v39-chaos-") as directory:
        runtime = DistributedRuntime(
            Path(directory) / "runtime.db", global_queue_limit=500,
            tenant_queue_limit=500, tenant_rate_per_minute=10_000,
        )

        def transient_handler(failures: int, final: dict[str, Any], message: str):
            calls = {"count": 0}

            def handle(_payload: dict[str, Any]) -> dict[str, Any]:
                calls["count"] += 1
                if calls["count"] <= failures:
                    raise RuntimeError(message)
                return final

            return calls, handle

        calls, handler = transient_handler(2, {"reconnected": True}, "network partition")
        attempts, result, status = _retry_scenario(
            runtime, fault="network_partition", pool="read_tool", handler=handler
        )
        scenarios.append(ChaosScenario(
            fault="network_partition", injection_point="read_tool.transport",
            attempts=attempts, recovered=status == "completed",
            terminal_class="success", expected_control="bounded retry with durable job state",
            evidence={"handler_calls": calls["count"], "result": result},
            passed=status == "completed" and attempts == 3,
        ))

        calls, handler = transient_handler(1, {"primary_reselected": True}, "database failover")
        attempts, result, status = _retry_scenario(
            runtime, fault="database_failover", pool="sql", handler=handler
        )
        scenarios.append(ChaosScenario(
            fault="database_failover", injection_point="repository.query",
            attempts=attempts, recovered=status == "completed", terminal_class="success",
            expected_control="transaction rollback and retry on a fresh lease",
            evidence={"handler_calls": calls["count"], "result": result},
            passed=status == "completed" and attempts == 2,
        ))

        def submit(_index: int) -> str:
            job, _ = runtime.enqueue(
                tenant_id="tenant_duplicate", pool="workflow", job_type="duplicate_delivery",
                idempotency_key="broker-delivery-42", payload={"event": 42},
                enforce_rate_limit=False,
            )
            return job.job_id

        with ThreadPoolExecutor(max_workers=12) as executor:
            ids = list(executor.map(submit, range(24)))
        scenarios.append(ChaosScenario(
            fault="duplicate_delivery", injection_point="queue.consumer",
            attempts=1, recovered=True, terminal_class="success",
            expected_control="tenant-scoped idempotency key",
            evidence={"deliveries": len(ids), "unique_jobs": len(set(ids))},
            passed=len(set(ids)) == 1,
        ))

        calls, handler = transient_handler(2, {"model_response": "valid"}, "429 rate limit")
        attempts, result, status = _retry_scenario(
            runtime, fault="model_rate_limit", pool="model", handler=handler
        )
        scenarios.append(ChaosScenario(
            fault="model_rate_limit", injection_point="model.adapter",
            attempts=attempts, recovered=status == "completed", terminal_class="success",
            expected_control="retry budget and provider backoff boundary",
            evidence={"handler_calls": calls["count"], "result": result},
            passed=status == "completed" and attempts == 3,
        ))

        calls, handler = transient_handler(
            1, {"degraded": True, "omitted": "optional_competitor_trend"}, "tool timeout"
        )
        attempts, result, status = _retry_scenario(
            runtime, fault="slow_tool", pool="read_tool", handler=handler
        )
        scenarios.append(ChaosScenario(
            fault="slow_tool", injection_point="optional_tool.deadline",
            attempts=attempts, recovered=status == "completed",
            terminal_class="degraded_completed",
            expected_control="deadline, bounded retry, then explicit optional degradation",
            evidence={"handler_calls": calls["count"], "result": result},
            passed=status == "completed" and bool(result and result.get("degraded")),
        ))

        conversation_path = Path(directory) / "conversation.db"
        repository = ConversationRepository(conversation_path)
        conversation = repository.create_conversation("tenant_chaos")
        reservation = repository.begin_turn(
            "tenant_chaos", conversation.conversation_id,
            client_request_id="req_chaos_summary", message="请帮我上架无线耳机",
        )
        repository.complete_message_turn(
            "tenant_chaos", conversation.conversation_id, reservation.turn.turn_id,
            intent="create_listing", assistant_message="请补充成本、售价和库存",
            response_payload={},
        )
        memory = ConversationMemoryService(repository)
        memory.refresh_summary("tenant_chaos", conversation.conversation_id)
        with sqlite3.connect(conversation_path) as connection:
            connection.execute(
                """UPDATE conversation_summaries SET fact_snapshot=?, content_hash=?
                WHERE tenant_id=? AND conversation_id=?""",
                ('{"cost":1,"target_price":999,"inventory":99999}', "forged",
                 "tenant_chaos", conversation.conversation_id),
            )
        seed = memory.context_seed("tenant_chaos", conversation.conversation_id)
        trust = seed["summary_trust"]
        summary_blocked = trust["valid"] is False and seed["conversation_summary"] == {}
        scenarios.append(ChaosScenario(
            fault="summary_pollution", injection_point="context.summary_cache",
            attempts=1, recovered=summary_blocked, terminal_class="waiting_user",
            expected_control="source replay, content hash and no write authority",
            evidence={"summary_trust": trust, "summary_used": bool(seed["conversation_summary"])},
            passed=summary_blocked and trust["write_authority"] is False,
        ))

    return ChaosReport(
        scenarios=tuple(scenarios),
        recovered_scenarios=sum(item.recovered for item in scenarios),
        total_scenarios=len(scenarios),
        passed=all(item.passed for item in scenarios),
        boundary=(
            "Faults are injected at protocol boundaries in a local SQLite reference runtime; "
            "this does not claim a real cloud network partition or database replica failover."
        ),
    )
