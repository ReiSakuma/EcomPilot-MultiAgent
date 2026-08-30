import json
import urllib.error
from pathlib import Path

from app.model.adapter import ModelAdapter, ModelProviderError
from app.model.pricing import estimate_cost_usd
from app.model.schemas import LISTING_JSON_SCHEMA
from app.orchestration.workflow import run_workflow


def test_v6_openai_provider_requires_api_key():
    adapter = ModelAdapter(provider="openai", model="gpt-5-mini", api_key="", max_retries=0)
    try:
        adapter.complete("hello")
    except ModelProviderError as exc:
        assert "Missing API key" in str(exc)
        return
    raise AssertionError("Expected ModelProviderError")


def test_v6_extracts_responses_output_text():
    adapter = ModelAdapter()
    body = {"output": [{"content": [{"type": "output_text", "text": '{"ok":true}'}]}]}

    assert adapter._extract_output_text(body) == '{"ok":true}'


def test_v6_cost_estimate_known_model():
    assert estimate_cost_usd("gpt-5-mini", 1_000_000, 1_000_000) == 2.25


def test_v6_llm_schema_path_still_runs_with_deterministic(monkeypatch):
    monkeypatch.setenv("ECOMPILOT_LLM_AGENTS", "listing_agent")
    state = run_workflow(
        "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，库存 800 件，毛利率不能低于 25%。",
        approved=True,
    )

    assert state.status == "completed"
    assert state.model_records
    assert state.agent_outputs["listing_agent"]["generation_mode"] == "llm"
    assert "cost_usd_estimate" in state.model_records[0]


def test_v6_schema_has_strict_required_fields():
    assert set(LISTING_JSON_SCHEMA["required"]) == {
        "title",
        "keywords",
        "bullets",
        "compliance_notes",
    }
