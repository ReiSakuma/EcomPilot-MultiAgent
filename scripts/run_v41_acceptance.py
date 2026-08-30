from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import PROJECT_VERSION
from app.copilot.compiler import RequestCompiler
from app.copilot.intents import RequestMode
from app.model.adapter import ModelResponse


class AcceptanceSemanticAdapter:
    provider = "deepseek"
    model = "deepseek-v4-pro-fixture"

    def __init__(self, output: dict) -> None:
        self.output = output
        self.calls = 0

    def complete(self, prompt: str, json_schema=None, *, max_output_tokens=None):
        self.calls += 1
        text = json.dumps(self.output, ensure_ascii=False)
        return ModelResponse(
            call_id=f"model_acceptance_{self.calls}",
            provider=self.provider,
            model=self.model,
            text=text,
            input_tokens=100,
            output_tokens=80,
            total_tokens=180,
            usage_source="acceptance_fixture",
            prompt_tokens_estimate=100,
            completion_tokens_estimate=80,
        )


def _field(name: str, value, quote: str) -> dict:
    return {
        "field_name": name,
        "value": value,
        "source_quote": quote,
        "confidence": 0.97,
        "extraction": "user_explicit",
        "normalization_note": "错别字归一化",
    }


def main() -> None:
    message = "我要上架无线耳机，成夲230园，售介250园，库洊800件，最低毛莉率30%。"
    output = {
        "intent": "create_listing",
        "confidence": 0.98,
        "category": None,
        "target_audience": None,
        "fields": [
            _field("category", "无线耳机", "无线耳机"),
            _field("cost", 230, "成夲230园"),
            _field("target_price", 250, "售介250园"),
            _field("inventory", 800, "库洊800件"),
            _field("min_margin_rate", 0.3, "最低毛莉率30%"),
        ],
        "explicitly_unknown_fields": [],
        "time_range_days": None,
        "topics": [],
        "unverified_requested_claims": [],
        "prompt_injection_detected": False,
        "rationale": "识别到带错别字的商品上架请求。",
    }
    adapter = AcceptanceSemanticAdapter(output)
    compiled = RequestCompiler(adapter).compile(message)  # type: ignore[arg-type]
    evidence_fields = {
        item.field_name
        for item in compiled.assessment.field_evidence
        if item.source == "model_extracted"
    }
    checks = {
        "project_version_0_41": PROJECT_VERSION == "0.41.0",
        "compiler_protocol_1_3": compiled.compiler_protocol_version == "1.3",
        "semantic_model_called_first": adapter.calls == 1,
        "typoed_fields_recovered": (
            compiled.structured_request["cost"] == 230
            and compiled.structured_request["target_price"] == 250
            and compiled.structured_request["inventory"] == 800
        ),
        "critical_fields_have_grounded_evidence": {
            "cost", "target_price", "inventory", "min_margin_rate"
        } <= evidence_fields,
        "deterministic_margin_policy_still_blocks": (
            compiled.assessment.mode is RequestMode.clarify
            and any(
                issue.code == "margin_infeasible"
                for issue in compiled.assessment.preflight_issues
            )
        ),
        "specialist_scopes_not_granted_after_rejection": (
            compiled.assessment.allowed_scopes == []
        ),
    }
    payload = {
        "version": PROJECT_VERSION,
        "release": "v41-semantic-compiler",
        "passed": all(checks.values()),
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
