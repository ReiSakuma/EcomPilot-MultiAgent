from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextSection(BaseModel):
    priority: Literal["P0", "P1", "P2", "P3", "P4", "P5", "P6"]
    name: str
    data: Any
    trusted: bool
    token_estimate: int = 0
    memory_refs: list[str] = Field(default_factory=list)


class ContextPackage(BaseModel):
    agent_name: str
    task_summary: str
    selected_parts: list[str] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    token_estimate: int = 0
    compressed: bool = False
    token_budget: int = 0
    sections: list[ContextSection] = Field(default_factory=list)
    dropped_sections: list[str] = Field(default_factory=list)
    protected_overflow: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join([self.task_summary, *self.selected_parts])


class BudgetedContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    priority: Literal["P0", "P1", "P2", "P3", "P4"]
    content: Any
    source_refs: list[str] = Field(default_factory=list)
    token_estimate: int = 0
    action: Literal["keep", "structure", "summarize", "drop"] = "keep"


class ContextBudgetDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["1.0"] = "1.0"
    context_window_tokens: int
    reserved_tokens: int
    input_capacity_tokens: int
    next_input_tokens: int
    selected_tokens: int
    compression_required: bool
    split_required: bool
    selected: list[BudgetedContextItem] = Field(default_factory=list)
    dropped_item_ids: list[str] = Field(default_factory=list)
    reason: str
