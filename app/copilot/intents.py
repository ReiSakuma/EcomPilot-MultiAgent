from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IntentName(str, Enum):
    create_listing = "create_listing"
    modify_listing = "modify_listing"
    market_research = "market_research"
    product_detail = "product_detail"
    product_performance = "product_performance"
    task_status = "task_status"
    remember_preference = "remember_preference"
    clarify = "clarify"
    general_chat = "general_chat"
    out_of_scope = "out_of_scope"


class RequestMode(str, Enum):
    execute = "execute"
    clarify = "clarify"
    advisory = "advisory"
    read_only = "read_only"
    general_chat = "general_chat"
    out_of_scope = "out_of_scope"


class PreflightIssue(BaseModel):
    """One user-safe reason why an expensive business workflow may not start."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "missing_required_fields",
        "invalid_field_value",
        "margin_infeasible",
        "conflicting_business_fields",
        "unverified_product_claim",
        "prompt_injection",
    ]
    category: Literal["input_contract", "business_rule", "content_safety", "security"]
    message: str = Field(min_length=2, max_length=240)
    field_path: str | None = None
    blocking: bool = True
    evidence: list[str] = Field(default_factory=list, max_length=12)


class FieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    value: Any = None
    source: Literal[
        "user_explicit", "user_context", "model_extracted", "model_inferred"
    ]
    confidence: float = Field(ge=0, le=1)
    required_for_write: bool = False


class CreateListingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    product_description: str = ""
    cost: float | None = Field(default=None, ge=0)
    target_price: float | None = Field(default=None, gt=0)
    inventory: int | None = Field(default=None, ge=0)
    min_margin_rate: float | None = Field(default=None, ge=0, lt=1)
    target_audience: str | None = None
    confirmed_features: list[str] = Field(default_factory=list)
    confirmed_product_form: str | None = None
    operation_goal: str | None = None


class MarketResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    product_description: str = ""
    target_audience: str | None = None
    time_range_days: int | None = Field(default=None, ge=1, le=3650)
    topics: list[Literal["price", "competition", "reviews", "demand"]] = Field(
        default_factory=list
    )


class TaskStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None


class ProductDetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    product_id: str | None = None
    sku: str | None = None
    task_id: str | None = None


class ProductPerformanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    product_id: str | None = None
    sku: str | None = None
    task_id: str | None = None
    start_date: str
    end_date: str
    period_label: str
    comparison_mode: Literal["none", "previous_period", "campaign_window"] = "none"


class FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["target_price", "inventory", "coupon", "title"]
    new_value: str | int | float
    source: Literal["user_explicit"] = "user_explicit"


class ModifyListingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    product_id: str | None = None
    sku: str | None = None
    task_id: str | None = None
    changes: list[FieldChange] = Field(min_length=1)


class GeneralChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


class MemoryPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    scope: str = "global"
    memory_type: str = "merchant_preference"
    conflict_key: str | None = None


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentName
    original_intent: IntentName | None = None
    confidence: float = Field(ge=0, le=1)
    rationale: str
    risk_level: Literal["none", "read", "write_plan"]
    data_scope: list[str] = Field(default_factory=list)


class IntentUnit(BaseModel):
    """One bounded unit in a user turn; dependencies must form an acyclic graph."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str
    intent: IntentName
    mode: Literal["read_only", "write_plan", "write_execute", "general_chat"]
    text: str
    entities: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    capability_scopes: list[str] = Field(default_factory=list)
    status: Literal["ready", "needs_clarification", "blocked"] = "ready"
    conflict_reason: str | None = None


class IntentExecutionGroup(BaseModel):
    """Intent units in one group may run concurrently after prior groups finish."""

    model_config = ConfigDict(extra="forbid")

    group_id: str
    intent_ids: list[str] = Field(min_length=1)
    execution: Literal["parallel", "serial"]
    risk_scope: Literal["none", "read", "write_plan", "write_execute"]


class RoutePlan(BaseModel):
    """Versioned route selected from the allowlisted workflow registry."""

    model_config = ConfigDict(extra="forbid")

    route_plan_version: Literal["1.0", "1.1"] = "1.1"
    route_id: str
    template_id: str
    intent: IntentName
    risk_scope: Literal["none", "read", "write_plan", "write_execute"]
    active_components: list[str] = Field(default_factory=list)
    planned_agents: list[str] = Field(default_factory=list)
    skipped_agents: list[str] = Field(default_factory=list)
    capability_scopes: list[str] = Field(default_factory=list)
    approval_required: bool = False
    stop_conditions: list[str] = Field(default_factory=list)
    intent_units: list[IntentUnit] = Field(default_factory=list, max_length=5)
    execution_groups: list[IntentExecutionGroup] = Field(default_factory=list)
    clarification_required: bool = False


class RequestAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RequestMode
    field_evidence: list[FieldEvidence] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    explicitly_unknown_fields: list[str] = Field(default_factory=list)
    proposed_workflow: str
    allowed_scopes: list[str] = Field(default_factory=list)
    approval_required: bool = False
    clarification_question: str | None = None
    clarification_round: int = Field(default=0, ge=0, le=3)
    preflight_status: Literal["passed", "needs_clarification", "blocked"] = "passed"
    preflight_issues: list[PreflightIssue] = Field(default_factory=list)
    rejected_claims: list[str] = Field(default_factory=list)


class BatchProductSpec(BaseModel):
    """One independently validated product inside a batch request."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    label: str
    source_text: str
    structured_request: dict[str, Any]
    assessment: RequestAssessment
    semantic_diagnostics: list[SemanticCompilerDiagnostic] = Field(default_factory=list)
    task_session_id: str | None = None


class BatchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    batch_job_id: str | None = None
    operation: Literal["create_listing"] = "create_listing"
    status: Literal["needs_clarification", "needs_confirmation", "ready", "blocked"]
    items: list[BatchProductSpec] = Field(min_length=2, max_length=5)


class CompiledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: IntentDecision
    assessment: RequestAssessment
    structured_request: dict[str, Any]
    compiler_model_records: list[dict[str, Any]] = Field(default_factory=list)
    route_plan: RoutePlan | None = None
    compiler_protocol_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
    semantic_status: Literal[
        "not_called",
        "model_validated",
        "repair_validated",
        "deterministic_fallback",
    ] = "not_called"
    semantic_diagnostics: list[SemanticCompilerDiagnostic] = Field(default_factory=list)
    intent_units: list[IntentUnit] = Field(default_factory=list, max_length=5)
    conflicts: list[str] = Field(default_factory=list)
    batch_plan: BatchPlan | None = None

    @model_validator(mode="after")
    def validate_intent_graph(self) -> "CompiledRequest":
        if not self.intent_units:
            return self
        ids = [unit.intent_id for unit in self.intent_units]
        if len(ids) != len(set(ids)):
            raise ValueError("intent_id values must be unique")
        known = set(ids)
        if any(dep not in known for unit in self.intent_units for dep in unit.dependencies):
            raise ValueError("intent dependency references an unknown intent_id")
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {unit.intent_id: unit for unit in self.intent_units}

        def visit(intent_id: str) -> None:
            if intent_id in visiting:
                raise ValueError("intent dependencies must form a DAG")
            if intent_id in visited:
                return
            visiting.add(intent_id)
            for dependency in by_id[intent_id].dependencies:
                visit(dependency)
            visiting.remove(intent_id)
            visited.add(intent_id)

        for intent_id in ids:
            visit(intent_id)
        return self


class SemanticFieldCandidate(BaseModel):
    """One untrusted field proposal that must point back to the user text."""

    model_config = ConfigDict(extra="forbid")

    field_name: Literal[
        "category",
        "cost",
        "target_price",
        "inventory",
        "min_margin_rate",
        "target_audience",
        "confirmed_features",
        "confirmed_product_form",
        "operation_goal",
    ]
    value: str | float | int | list[str] | None = None
    source_quote: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0, le=1)
    extraction: Literal["user_explicit", "model_inferred", "explicitly_unknown"]
    normalization_note: str | None = Field(default=None, max_length=160)


class SemanticCompilerDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str | None = None
    code: Literal[
        "accepted",
        "low_confidence",
        "source_not_grounded",
        "unsafe_inference",
        "invalid_value",
        "deterministic_override",
        "model_failure",
        "schema_repaired",
        "length_retried",
    ]
    message: str = Field(min_length=2, max_length=300)
    source_quote: str | None = Field(default=None, max_length=240)


class BatchProductCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    source_text: str = Field(min_length=2, max_length=800)
    fields: list[SemanticFieldCandidate] = Field(default_factory=list, max_length=16)
    explicitly_unknown_fields: list[str] = Field(default_factory=list, max_length=8)
    unverified_requested_claims: list[str] = Field(default_factory=list, max_length=8)


class BatchCompilerModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BatchProductCandidate] = Field(min_length=2, max_length=5)
    shared_fields: list[SemanticFieldCandidate] = Field(default_factory=list, max_length=12)
    prompt_injection_detected: bool = False
    rationale: str = Field(min_length=2, max_length=200)


class CompilerModelOutput(BaseModel):
    """Untrusted semantic proposal returned by the LLM before policy validation."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "create_listing",
        "modify_listing",
        "market_research",
        "product_detail",
        "product_performance",
        "task_status",
        "remember_preference",
        "general_chat",
        "out_of_scope",
    ]
    confidence: float = Field(ge=0, le=1)
    category: str | None = None
    target_audience: str | None = None
    fields: list[SemanticFieldCandidate] = Field(default_factory=list, max_length=16)
    explicitly_unknown_fields: list[
        Literal[
            "category",
            "cost",
            "target_price",
            "inventory",
            "min_margin_rate",
            "target_audience",
            "confirmed_features",
            "confirmed_product_form",
        ]
    ] = Field(default_factory=list, max_length=8)
    time_range_days: int | None = Field(default=None, ge=1, le=3650)
    topics: list[Literal["price", "competition", "reviews", "demand"]] = Field(
        default_factory=list
    )
    unverified_requested_claims: list[str] = Field(default_factory=list, max_length=8)
    prompt_injection_detected: bool = False
    rationale: str = Field(min_length=2, max_length=160)
