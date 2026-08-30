from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.copilot.intents import IntentExecutionGroup, IntentUnit, RoutePlan


class IntentUnitResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    status: Literal["completed", "failed", "blocked"]
    output: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime
    completed_at: datetime


class MultiIntentExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    route_id: str
    status: Literal["completed", "partial", "failed", "needs_clarification"]
    group_order: list[str] = Field(default_factory=list)
    results: list[IntentUnitResult] = Field(default_factory=list)


UnitExecutor = Callable[[IntentUnit, dict[str, IntentUnitResult]], IntentUnitResult | dict[str, Any]]


class MultiIntentExecutor:
    """Bounded DAG scheduler: read groups may fan out, write groups remain serial."""

    def __init__(self, *, max_parallel_reads: int = 4) -> None:
        self.max_parallel_reads = max(1, min(max_parallel_reads, 4))

    def execute(self, plan: RoutePlan, execute_unit: UnitExecutor) -> MultiIntentExecutionReport:
        if plan.clarification_required:
            return MultiIntentExecutionReport(
                route_id=plan.route_id,
                status="needs_clarification",
                group_order=[group.group_id for group in plan.execution_groups],
            )
        units = {unit.intent_id: unit for unit in plan.intent_units}
        results: dict[str, IntentUnitResult] = {}
        for group in plan.execution_groups:
            blocked = [
                intent_id for intent_id in group.intent_ids
                if any(
                    dependency not in results or results[dependency].status != "completed"
                    for dependency in units[intent_id].dependencies
                )
            ]
            for intent_id in blocked:
                now = datetime.now(timezone.utc)
                results[intent_id] = IntentUnitResult(
                    intent_id=intent_id, status="blocked",
                    error="dependency_not_completed", started_at=now, completed_at=now,
                )
            runnable = [intent_id for intent_id in group.intent_ids if intent_id not in blocked]
            if group.execution == "parallel" and group.risk_scope == "read" and len(runnable) > 1:
                with ThreadPoolExecutor(
                    max_workers=min(self.max_parallel_reads, len(runnable)),
                    thread_name_prefix="intent-read",
                ) as pool:
                    futures = {
                        pool.submit(self._run_one, units[intent_id], execute_unit, dict(results)): intent_id
                        for intent_id in runnable
                    }
                    for future in as_completed(futures):
                        results[futures[future]] = future.result()
            else:
                for intent_id in runnable:
                    results[intent_id] = self._run_one(
                        units[intent_id], execute_unit, dict(results)
                    )
        ordered = [results[unit.intent_id] for unit in plan.intent_units if unit.intent_id in results]
        failed = sum(item.status == "failed" for item in ordered)
        blocked = sum(item.status == "blocked" for item in ordered)
        status = "completed" if not failed and not blocked else "failed" if failed == len(ordered) else "partial"
        return MultiIntentExecutionReport(
            route_id=plan.route_id,
            status=status,
            group_order=[group.group_id for group in plan.execution_groups],
            results=ordered,
        )

    @staticmethod
    def _run_one(
        unit: IntentUnit,
        execute_unit: UnitExecutor,
        dependencies: dict[str, IntentUnitResult],
    ) -> IntentUnitResult:
        started = datetime.now(timezone.utc)
        try:
            result = execute_unit(unit.model_copy(deep=True), dependencies)
            if isinstance(result, IntentUnitResult):
                return result
            return IntentUnitResult(
                intent_id=unit.intent_id,
                status="completed",
                output=dict(result),
                artifact_refs=list(result.get("artifact_refs", [])),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            return IntentUnitResult(
                intent_id=unit.intent_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
