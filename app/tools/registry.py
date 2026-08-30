from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from time import perf_counter
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from app.access.context import tenant_scope
from app.distributed.bulkhead import GLOBAL_BULKHEADS
from app.tools.analytics_tools import (
    compare_sales_periods,
    get_campaign_performance,
    get_inventory_history,
    get_sales_metrics,
)
from app.safety.permissions import (
    RiskLevel,
    ToolApprovalRequiredError,
    assert_tool_permission,
)
from app.tools.browser_tools import browser_execute, browser_verify, get_seller_center_snapshot
from app.tools.contracts import (
    AnalyticsRangeInput,
    BrowserExecuteInput,
    BrowserVerifyInput,
    CampaignHistoryInput,
    CompetitorPriceTrendInput,
    DemandForecastInput,
    DiscountInput,
    EmptyInput,
    FeatureAnalysisInput,
    InventoryInput,
    KeywordSearchInput,
    MarginInput,
    MarketReportInput,
    ProductSearchInput,
    ReviewAnalysisInput,
    ReviewSearchInput,
    SqlQueryInput,
)
from app.tools.inventory_tools import check_inventory
from app.tools.pricing_tools import (
    calculate_margin,
    suggest_discount_amount_yuan,
)
from app.tools.strategy_evidence_tools import (
    analyze_competitor_price_trends,
    forecast_demand,
    query_campaign_history,
)
from app.tools.product_tools import (
    analyze_feature_frequency,
    analyze_review_pain_points,
    build_market_report,
    get_reviews,
    search_keywords,
    search_products,
)
from app.tools.sql_tools import query_market_database
from app.tools.schemas import (
    ToolCallRecord,
    ToolParameterError,
    ToolResultValidationError,
    ToolSpec,
    ToolTimeoutError,
    TransientToolError,
    UnknownWriteStateError,
)
from app.reliability.circuit_breaker import CircuitBreakerRegistry
from app.reliability.classifier import build_error_signature, classify_failure
from app.reliability.models import FailureTaxonomy, RetryBudget
from app.reliability.policy import retry_decision
from app.security.capability_tokens import (
    CapabilityAuthorizationError,
    CapabilityAuthority,
)


ResultValidator = Callable[[Any], None]
GLOBAL_CIRCUITS = CircuitBreakerRegistry()
TASK_RESULT_CACHE_MAX_TASKS = 2048
TASK_RESULT_CACHE_MAX_ENTRIES = 128
TASK_RESULT_CACHE_TOOLS = frozenset(
    {
        "forecast_demand",
        "query_campaign_history",
        "analyze_competitor_price_trends",
        "suggest_discount",
        "calculate_margin",
        "check_inventory",
    }
)


class ToolRegistry:
    def __init__(
        self,
        capability_authority: CapabilityAuthority | None = None,
        *,
        require_capability_token: bool = False,
        circuit_registry: CircuitBreakerRegistry | None = None,
    ) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._input_models: dict[str, type[BaseModel]] = {}
        self._result_validators: dict[str, ResultValidator] = {}
        self.call_records: list[ToolCallRecord] = []
        self._observer: Callable[[dict[str, Any]], None] | None = None
        self._agent_context: ContextVar[str] = ContextVar("tool_agent_name", default="unknown")
        self._approval_context: ContextVar[bool] = ContextVar("tool_approved", default=False)
        self._approver_context: ContextVar[str | None] = ContextVar(
            "tool_approved_by", default=None
        )
        self._task_context: ContextVar[str] = ContextVar("tool_task_id", default="")
        self._tenant_context: ContextVar[str] = ContextVar(
            "tool_tenant_id", default="tenant_demo"
        )
        self._delegation_context: ContextVar[str] = ContextVar(
            "tool_delegation_id", default=""
        )
        self._capability_context: ContextVar[str] = ContextVar(
            "tool_capability_id", default=""
        )
        self._capability_token_context: ContextVar[str] = ContextVar(
            "tool_capability_token", default=""
        )
        self._capability_token_id_context: ContextVar[str] = ContextVar(
            "tool_capability_token_id", default=""
        )
        self._capability_authority = capability_authority
        self._require_capability_token = require_capability_token
        self._circuits = circuit_registry or CircuitBreakerRegistry()
        self._retry_budgets: dict[str, RetryBudget] = {}
        self._recovery_results: dict[str, dict[str, Any]] = {}
        self._task_result_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._result_cache_lock = RLock()
        if require_capability_token and capability_authority is None:
            raise ValueError("Capability enforcement requires a CapabilityAuthority")
        self._register_defaults()

    def _register_defaults(self) -> None:
        market_agents = {"market_agent"}
        strategy_agents = {"strategy_agent"}
        browser_agents = {"browser_agent"}
        analytics_agents = {"analytics_agent"}
        self.register(
            ToolSpec(name="search_products", allowed_agents=market_agents, input_schema="ProductSearchInput"),
            search_products,
            ProductSearchInput,
            _validate_list_result,
        )
        self.register(
            ToolSpec(name="search_keywords", allowed_agents=market_agents, input_schema="KeywordSearchInput"),
            search_keywords,
            KeywordSearchInput,
            _validate_list_result,
        )
        self.register(
            ToolSpec(name="get_reviews", allowed_agents=market_agents, input_schema="ReviewSearchInput"),
            get_reviews,
            ReviewSearchInput,
            _validate_list_result,
        )
        self.register(
            ToolSpec(
                name="analyze_review_pain_points",
                allowed_agents=market_agents,
                input_schema="ReviewAnalysisInput",
            ),
            analyze_review_pain_points,
            ReviewAnalysisInput,
            lambda result: _require_dict_fields(result, {"pain_points", "pain_point_counts"}),
        )
        self.register(
            ToolSpec(
                name="analyze_feature_frequency",
                allowed_agents=market_agents,
                input_schema="FeatureAnalysisInput",
            ),
            analyze_feature_frequency,
            FeatureAnalysisInput,
            lambda result: _require_dict_fields(result, {"top_features", "feature_counts"}),
        )
        self.register(
            ToolSpec(
                name="build_market_report",
                allowed_agents=market_agents,
                input_schema="MarketReportInput",
                timeout_seconds=3,
            ),
            build_market_report,
            MarketReportInput,
            _validate_market_report,
        )
        self.register(
            ToolSpec(
                name="query_market_database",
                allowed_agents=market_agents,
                input_schema="SqlQueryInput",
                timeout_seconds=2,
                max_retries=0,
            ),
            query_market_database,
            SqlQueryInput,
            _validate_sql_query_result,
        )
        self.register(
            ToolSpec(name="calculate_margin", allowed_agents=strategy_agents, input_schema="MarginInput"),
            calculate_margin,
            MarginInput,
            _validate_margin_result,
        )
        self.register(
            ToolSpec(
                name="suggest_discount",
                allowed_agents=strategy_agents,
                input_schema="DiscountInput",
                result_schema="PromotionSuggestionV1",
            ),
            suggest_discount_amount_yuan,
            DiscountInput,
            _validate_discount_amount_result,
        )
        self.register(
            ToolSpec(
                name="suggest_discount_amount_yuan",
                allowed_agents=strategy_agents,
                input_schema="DiscountInput",
                result_schema="PromotionSuggestionV1",
            ),
            suggest_discount_amount_yuan,
            DiscountInput,
            _validate_discount_amount_result,
        )
        self.register(
            ToolSpec(name="check_inventory", allowed_agents=strategy_agents, input_schema="InventoryInput"),
            check_inventory,
            InventoryInput,
            _validate_inventory_result,
        )
        self.register(
            ToolSpec(
                name="forecast_demand",
                allowed_agents=strategy_agents,
                input_schema="DemandForecastInput",
            ),
            forecast_demand,
            DemandForecastInput,
            lambda result: _require_dict_fields(
                result, {"status", "forecast_units", "evidence_refs", "source_type"}
            ),
        )
        self.register(
            ToolSpec(
                name="query_campaign_history",
                allowed_agents=strategy_agents,
                input_schema="CampaignHistoryInput",
            ),
            query_campaign_history,
            CampaignHistoryInput,
            lambda result: _require_dict_fields(
                result, {"status", "campaigns", "summary", "evidence_refs", "source_type"}
            ),
        )
        self.register(
            ToolSpec(
                name="analyze_competitor_price_trends",
                allowed_agents=strategy_agents,
                input_schema="CompetitorPriceTrendInput",
            ),
            analyze_competitor_price_trends,
            CompetitorPriceTrendInput,
            lambda result: _require_dict_fields(
                result, {"status", "trends", "summary", "evidence_refs", "source_type"}
            ),
        )
        for name, function, required_fields in (
            (
                "get_sales_metrics",
                get_sales_metrics,
                {"status", "product_id", "period", "metrics", "source_type", "source_updated_at"},
            ),
            (
                "compare_sales_periods",
                compare_sales_periods,
                {"status", "product_id", "current_period", "previous_period", "change", "source_type"},
            ),
            (
                "get_campaign_performance",
                get_campaign_performance,
                {"status", "product_id", "period", "campaigns", "summary", "source_type"},
            ),
            (
                "get_inventory_history",
                get_inventory_history,
                {"status", "product_id", "period", "ending_inventory", "source_type"},
            ),
        ):
            self.register(
                ToolSpec(
                    name=name,
                    allowed_agents=analytics_agents,
                    input_schema="AnalyticsRangeInput",
                    timeout_seconds=3,
                    max_retries=1,
                ),
                function,
                AnalyticsRangeInput,
                lambda result, fields=required_fields: _require_dict_fields(result, fields),
            )
        self.register(
            ToolSpec(
                name="browser_execute",
                risk_level=RiskLevel.high,
                side_effect=True,
                requires_approval=True,
                allowed_agents=browser_agents,
                input_schema="BrowserExecuteInput",
                timeout_seconds=45,
                max_retries=0,
                operation_type="write",
                idempotency="keyed",
                compensation="manual_store_rollback",
                reconcile_tool="browser_verify",
                concurrency_limit=1,
            ),
            browser_execute,
            BrowserExecuteInput,
            _validate_browser_execute_result,
        )
        self.register(
            ToolSpec(
                name="browser_verify",
                allowed_agents=browser_agents,
                input_schema="BrowserVerifyInput",
                timeout_seconds=45,
            ),
            browser_verify,
            BrowserVerifyInput,
            _validate_browser_verify_result,
        )
        self.register(
            ToolSpec(
                name="get_seller_center_snapshot",
                allowed_agents={"browser_agent", "supervisor"},
                input_schema="EmptyInput",
            ),
            get_seller_center_snapshot,
            EmptyInput,
            lambda result: _require_dict_fields(result, {"products", "promotions"}),
        )

    def register(
        self,
        spec: ToolSpec,
        function: Callable[..., Any],
        input_model: type[BaseModel],
        result_validator: ResultValidator,
    ) -> None:
        self._tools[spec.name] = function
        self._specs[spec.name] = spec
        self._input_models[spec.name] = input_model
        self._result_validators[spec.name] = result_validator

    def set_observer(self, observer: Callable[[dict[str, Any]], None] | None) -> None:
        self._observer = observer

    def bind_retry_budget(self, task_id: str, budget: RetryBudget) -> None:
        self._retry_budgets[task_id] = budget.model_copy(deep=True)

    def retry_budget(self, task_id: str) -> RetryBudget:
        return self._retry_budgets.setdefault(task_id, RetryBudget()).model_copy(deep=True)

    def seed_recovery_results(self, task_id: str, records: list[dict[str, Any]]) -> None:
        cache: dict[str, Any] = {}
        for record in records:
            if (
                record.get("status") == "completed"
                and not record.get("side_effect")
                and record.get("input_hash")
                and record.get("result_summary") is not None
            ):
                cache[record["input_hash"]] = record["result_summary"]
        with self._result_cache_lock:
            self._recovery_results[task_id] = cache

    def clear_task_result_cache(self, task_id: str) -> None:
        """Release task-scoped read snapshots after their recovery window ends."""

        with self._result_cache_lock:
            self._recovery_results.pop(task_id, None)
            self._task_result_cache.pop(task_id, None)

    def _cache_task_result(self, task_id: str, input_hash: str, result: Any) -> None:
        cache = self._task_result_cache.setdefault(task_id, {})
        cache[input_hash] = deepcopy(result)
        while len(cache) > TASK_RESULT_CACHE_MAX_ENTRIES:
            cache.pop(next(iter(cache)))
        self._task_result_cache.move_to_end(task_id)
        self._trim_result_cache_locked()

    def _trim_result_cache_locked(self) -> None:
        while len(self._task_result_cache) > TASK_RESULT_CACHE_MAX_TASKS:
            self._task_result_cache.popitem(last=False)

    @contextmanager
    def agent_scope(
        self,
        agent_name: str,
        *,
        approved: bool = False,
        approved_by: str | None = None,
        task_id: str = "",
        tenant_id: str | None = None,
        delegation_id: str | None = None,
        capability_id: str | None = None,
        capability_token: str | None = None,
        capability_token_id: str | None = None,
    ):
        effective_delegation = delegation_id or self._delegation_context.get()
        effective_capability = capability_id or self._capability_context.get()
        effective_token = capability_token or self._capability_token_context.get()
        effective_token_id = capability_token_id or self._capability_token_id_context.get()
        effective_tenant = tenant_id or self._tenant_context.get()
        agent_token = self._agent_context.set(agent_name)
        approval_token = self._approval_context.set(approved)
        approver_token = self._approver_context.set(approved_by if approved else None)
        task_token = self._task_context.set(task_id)
        tenant_token = self._tenant_context.set(effective_tenant)
        delegation_token = self._delegation_context.set(effective_delegation)
        capability_id_token = self._capability_context.set(effective_capability)
        capability_token_token = self._capability_token_context.set(effective_token)
        capability_token_id_token = self._capability_token_id_context.set(effective_token_id)
        try:
            with tenant_scope(effective_tenant):
                yield
        finally:
            self._agent_context.reset(agent_token)
            self._approval_context.reset(approval_token)
            self._approver_context.reset(approver_token)
            self._task_context.reset(task_token)
            self._tenant_context.reset(tenant_token)
            self._delegation_context.reset(delegation_token)
            self._capability_context.reset(capability_id_token)
            self._capability_token_context.reset(capability_token_token)
            self._capability_token_id_context.reset(capability_token_id_token)

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        spec = self._specs[name]
        call_id = f"tool_{uuid4().hex[:12]}"
        agent_name = self._agent_context.get()
        task_id = self._task_context.get()
        tenant_id = self._tenant_context.get()
        delegation_id = self._delegation_context.get() or None
        capability_id = self._capability_context.get() or None
        capability_token_id = self._capability_token_id_context.get() or None
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        attempts = 0
        validation_status = "not_run"
        normalized_args = kwargs
        input_hash = self._input_hash(name, kwargs, tenant_id)
        output_hash: str | None = None
        recovered_result = False
        retry_decisions: list[dict[str, Any]] = []
        circuit_key = f"{tenant_id}:{name}"
        circuit_state = "closed"
        try:
            circuit_state = self._circuits.before_call(
                circuit_key, threshold=spec.circuit_failure_threshold
            ).state
            assert_tool_permission(agent_name, name, spec.allowed_agents or None)
            validation_status = "permission_validated"
            if spec.requires_approval and not self._approval_context.get():
                raise ToolApprovalRequiredError(
                    f"Tool '{name}' requires explicit human approval"
                )
            validation_status = "approval_validated"
            if self._require_capability_token:
                assert self._capability_authority is not None
                if not delegation_id or not capability_id or not self._capability_token_context.get():
                    self._capability_authority.deny(
                        task_id=task_id,
                        delegation_id=delegation_id or "missing",
                        capability_id=capability_id or "missing",
                        agent_name=agent_name,
                        tool_name=name,
                        reason="A delegation-bound capability token is required",
                    )
                claims = self._capability_authority.verify_and_consume(
                    self._capability_token_context.get(),
                    task_id=task_id,
                    delegation_id=delegation_id,
                    capability_id=capability_id,
                    agent_name=agent_name,
                    tool_name=name,
                    tenant_id=tenant_id,
                )
                capability_token_id = claims.token_id
                validation_status = "capability_validated"
            normalized_args = self._validate_input(name, kwargs)
            validation_status = "input_validated"
            input_hash = self._input_hash(name, normalized_args, tenant_id)
            with self._result_cache_lock:
                cached = deepcopy(self._recovery_results.get(task_id, {}).get(input_hash))
                if cached is None and name in TASK_RESULT_CACHE_TOOLS:
                    cached = deepcopy(
                        self._task_result_cache.get(task_id, {}).get(input_hash)
                    )
                    if cached is not None and task_id in self._task_result_cache:
                        self._task_result_cache.move_to_end(task_id)
            if cached is not None and spec.operation_type == "read":
                result = cached
                attempts = 0
                recovered_result = True
                validation_status = "recovered_result"
            else:
                pool = "write_tool" if spec.operation_type == "write" else "read_tool"
                with GLOBAL_BULKHEADS.acquire(pool, blocking=True):
                    result, attempts, retry_decisions = self._execute_with_retries(
                        spec, normalized_args, task_id=task_id, circuit_key=circuit_key
                    )
            self._result_validators[name](result)
            validation_status = "result_validated"
            output_hash = self._value_hash(result)
            if (
                task_id
                and spec.operation_type == "read"
                and spec.idempotency == "safe"
                and name in TASK_RESULT_CACHE_TOOLS
                and not recovered_result
            ):
                with self._result_cache_lock:
                    self._cache_task_result(task_id, input_hash, result)
            self._circuits.success(circuit_key)
        except Exception as exc:
            attempts = max(attempts, int(getattr(exc, "tool_attempt_count", 0)))
            category = classify_failure(exc)
            signature = build_error_signature(
                exc, agent_name=agent_name, tool_name=name
            )
            if category.value in {"transient", "rate_limit", "unknown"}:
                circuit = self._circuits.failure(
                    circuit_key, signature, threshold=spec.circuit_failure_threshold
                )
                circuit_state = circuit.state
            status = "unknown" if isinstance(exc, UnknownWriteStateError) else "failed"
            record = ToolCallRecord(
                call_id=call_id,
                tool_name=name,
                args=kwargs,
                status=status,
                risk_level=spec.risk_level,
                side_effect=spec.side_effect,
                agent_name=agent_name,
                task_id=task_id,
                tenant_id=tenant_id,
                delegation_id=delegation_id,
                capability_id=capability_id,
                capability_token_id=capability_token_id,
                approved_by=self._approver_context.get(),
                started_at=started_at,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                attempt_count=max(1, attempts),
                validation_status=validation_status,
                input_hash=input_hash,
                output_hash=output_hash,
                failure_category=category.value,
                error_signature=signature,
                circuit_state=circuit_state,
                retry_decisions=retry_decisions,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.call_records.append(record)
            self._notify(record, result=None)
            raise
        record_args = dict(normalized_args)
        if name == "calculate_margin" and "discount" in kwargs:
            # Preserve the exact legacy request in audit records while execution
            # and all new callers use the canonical yuan-denominated field.
            record_args["discount"] = kwargs["discount"]
        record = ToolCallRecord(
            call_id=call_id,
            tool_name=name,
            args=record_args,
            status="completed",
            risk_level=spec.risk_level,
            side_effect=spec.side_effect,
            agent_name=agent_name,
            task_id=task_id,
            tenant_id=tenant_id,
            delegation_id=delegation_id,
            capability_id=capability_id,
            capability_token_id=capability_token_id,
            approved_by=self._approver_context.get(),
            started_at=started_at,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            attempt_count=attempts,
            validation_status=validation_status,
            idempotent_replay=bool(
                isinstance(result, dict) and result.get("idempotent_replay")
            ),
            recovered_result=recovered_result,
            input_hash=input_hash,
            output_hash=output_hash,
            circuit_state=circuit_state,
            retry_decisions=retry_decisions,
            result_summary=result,
        )
        self.call_records.append(record)
        self._notify(record, result=result)
        return result

    def records(self, task_id: str | None = None) -> list[ToolCallRecord]:
        records = list(self.call_records)
        if task_id is None:
            return records
        return [record for record in records if record.task_id == task_id]

    def spec(self, name: str) -> ToolSpec:
        return self._specs[name].model_copy(deep=True)

    def specs(self) -> dict[str, ToolSpec]:
        return {
            name: spec.model_copy(deep=True) for name, spec in self._specs.items()
        }

    def input_model(self, name: str) -> type[BaseModel]:
        return self._input_models[name]

    def describe_tools(self) -> list[dict[str, Any]]:
        descriptions: list[dict[str, Any]] = []
        for name, spec in sorted(self._specs.items(), key=lambda item: item[0]):
            input_contract = self._input_models[name].model_json_schema()
            payload = spec.model_dump(mode="json")
            payload["allowed_agents"] = sorted(spec.allowed_agents)
            payload["required_args"] = input_contract.get("required", [])
            payload["input_contract"] = input_contract
            descriptions.append(payload)
        return descriptions

    def _validate_input(self, name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = self._input_models[name].model_validate(kwargs)
        except ValidationError as exc:
            raise ToolParameterError(f"Invalid arguments for '{name}': {exc}") from exc
        return validated.model_dump(mode="json", exclude_none=True)

    def _execute_with_retries(
        self,
        spec: ToolSpec,
        kwargs: dict[str, Any],
        *,
        task_id: str,
        circuit_key: str,
    ) -> tuple[Any, int, list[dict[str, Any]]]:
        decisions: list[dict[str, Any]] = []
        budget = self._retry_budgets.setdefault(task_id, RetryBudget())
        for attempt in range(1, spec.max_retries + 2):
            try:
                return self._execute_with_timeout(
                    self._tools[spec.name], kwargs, spec.timeout_seconds, spec.name
                ), attempt, decisions
            except Exception as exc:
                category = classify_failure(exc)
                if spec.side_effect and category in {
                    FailureTaxonomy.transient,
                    FailureTaxonomy.rate_limit,
                    FailureTaxonomy.unknown,
                }:
                    unknown = UnknownWriteStateError(
                        f"Write tool '{spec.name}' did not confirm completion; execution state is unknown. "
                        f"Read back with '{spec.reconcile_tool or 'a reconciliation tool'}' before retrying."
                    )
                    setattr(unknown, "tool_attempt_count", attempt)
                    raise unknown from exc
                signature = build_error_signature(
                    exc,
                    agent_name=self._agent_context.get(),
                    tool_name=spec.name,
                )
                decision = retry_decision(
                    component=circuit_key,
                    category=category,
                    signature=signature,
                    attempt=attempt,
                    budget_remaining=budget.remaining,
                    retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                )
                if category.value not in spec.retryable_errors:
                    decision = decision.model_copy(
                        update={"allowed": False, "reason": "tool_declaration_disallows_retry"}
                    )
                if attempt > spec.max_retries:
                    decision = decision.model_copy(
                        update={"allowed": False, "reason": "tool_attempt_limit_reached"}
                    )
                decisions.append(decision.model_dump(mode="json"))
                if not budget.consume(decision):
                    setattr(exc, "tool_attempt_count", attempt)
                    raise
                time.sleep(decision.delay_seconds)
        raise RuntimeError("unreachable retry state")

    @staticmethod
    def _input_hash(name: str, kwargs: dict[str, Any], tenant_id: str) -> str:
        return hashlib.sha256(
            json.dumps(
                {"tool": name, "tenant": tenant_id, "args": kwargs},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _value_hash(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _execute_with_timeout(
        function: Callable[..., Any], kwargs: dict[str, Any], timeout_seconds: float, name: str
    ) -> Any:
        pool = ThreadPoolExecutor(max_workers=1)
        context = copy_context()
        future = pool.submit(context.run, function, **kwargs)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise ToolTimeoutError(
                f"Tool '{name}' exceeded timeout of {timeout_seconds} seconds"
            ) from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _notify(self, record: ToolCallRecord, result: Any) -> None:
        if self._observer is None:
            return
        try:
            self._observer(
                {
                    "event_type": "tool_call",
                    "component_type": "tool",
                    "component_name": record.tool_name,
                    "agent_name": record.agent_name,
                    "step": record.tool_name,
                    "status": record.status,
                    "duration_ms": record.duration_ms,
                    "details": {
                        "call_id": record.call_id,
                        "task_id": record.task_id,
                        "tenant_id": record.tenant_id,
                        "delegation_id": record.delegation_id,
                        "capability_id": record.capability_id,
                        "capability_token_id": record.capability_token_id,
                        "approved_by": record.approved_by,
                        "args": record.args,
                        "result": result,
                        "risk_level": record.risk_level.value,
                        "side_effect": record.side_effect,
                        "attempt_count": record.attempt_count,
                        "validation_status": record.validation_status,
                        "idempotent_replay": record.idempotent_replay,
                        "recovered_result": record.recovered_result,
                        "input_hash": record.input_hash,
                        "output_hash": record.output_hash,
                        "failure_category": record.failure_category,
                        "error_signature": record.error_signature,
                        "circuit_state": record.circuit_state,
                        "retry_decisions": record.retry_decisions,
                    },
                    "error": {
                        "type": record.error_type,
                        "message": record.error,
                    }
                    if record.error
                    else None,
                }
            )
        except Exception:
            return


def _validate_list_result(result: Any) -> None:
    if not isinstance(result, list):
        raise ToolResultValidationError("Expected list result")


def _require_dict_fields(result: Any, fields: set[str]) -> None:
    if not isinstance(result, dict):
        raise ToolResultValidationError("Expected object result")
    missing = fields - set(result)
    if missing:
        raise ToolResultValidationError(f"Result missing fields: {sorted(missing)}")


def _validate_market_report(result: Any) -> None:
    _require_dict_fields(
        result,
        {
            "sample_size", "price_band", "competitors", "keywords",
            "evidence_refs", "market_statistics", "market_layers",
        },
    )
    _require_dict_fields(result["sample_size"], {"competitors", "reviews"})
    _require_dict_fields(
        result["market_statistics"],
        {
            "mode", "input_count", "retained_count", "excluded_count",
            "decisions", "content_hash",
        },
    )
    _require_dict_fields(
        result["market_layers"],
        {
            "core_reference_price", "reference_method", "core_comparable",
            "adjacent_tier", "full_valid_market", "decisions", "content_hash",
        },
    )


def _validate_sql_query_result(result: Any) -> None:
    _require_dict_fields(
        result,
        {"query_id", "normalized_sql", "columns", "rows", "row_count", "policy"},
    )
    _require_dict_fields(
        result["policy"], {"status", "tables", "enforced_limit", "read_only_connection"}
    )
    if result["policy"]["status"] != "allowed":
        raise ToolResultValidationError("SQL policy did not allow the query")
    if int(result["row_count"]) > int(result["policy"]["enforced_limit"]):
        raise ToolResultValidationError("SQL row count exceeds enforced limit")


def _validate_margin_result(result: Any) -> None:
    _require_dict_fields(
        result,
        {
            "promotion_protocol_version",
            "discount_amount_yuan",
            "net_price",
            "margin",
            "margin_rate",
            "cost",
        },
    )
    if not -1 <= float(result["margin_rate"]) <= 1:
        raise ToolResultValidationError("margin_rate must be between -1 and 1")


def _validate_discount_result(result: Any) -> None:
    if not isinstance(result, (int, float)) or result < 0:
        raise ToolResultValidationError("discount must be a non-negative number")


def _validate_discount_amount_result(result: Any) -> None:
    _require_dict_fields(
        result,
        {
            "promotion_protocol_version",
            "discount_amount_yuan",
            "currency",
            "promotion",
        },
    )
    if float(result["discount_amount_yuan"]) < 0 or result["currency"] != "CNY":
        raise ToolResultValidationError("discount amount must be non-negative CNY")


def _validate_inventory_result(result: Any) -> None:
    _require_dict_fields(result, {"inventory", "planned_units", "valid", "remaining"})
    expected = int(result["inventory"]) - int(result["planned_units"])
    if int(result["remaining"]) != expected:
        raise ToolResultValidationError("inventory remaining value is inconsistent")


def _validate_browser_execute_result(result: Any) -> None:
    _require_dict_fields(
        result,
        {"status", "product_id", "operation", "verification", "backend", "actions"},
    )
    if result["status"] != "applied":
        raise ToolResultValidationError("seller-center operation was not applied")


def _validate_browser_verify_result(result: Any) -> None:
    _require_dict_fields(result, {"verified", "checks", "observed", "errors", "backend", "actions"})
    if not isinstance(result["verified"], bool):
        raise ToolResultValidationError("verified must be boolean")
