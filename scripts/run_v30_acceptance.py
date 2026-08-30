from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.conversations.repository import ConversationRepository
from app.orchestration.workflow import run_workflow
from app.products.ledger import ProductLedger
from app.products.resolver import EntityResolver


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "summaries"


def _record(repository: ConversationRepository, conversation_id: str, goal: str):
    state = run_workflow(goal, approved=True)
    state.conversation_id = conversation_id
    state.turn_id = f"turn_{state.task_id}"
    return state, ProductLedger(repository.database_path).record_successful_execution(state)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ecompilot_v30_") as temporary:
        database = Path(temporary) / "conversation.db"
        repository = ConversationRepository(database)
        conversation = repository.create_conversation("tenant_demo")
        state_a, product_a = _record(
            repository,
            conversation.conversation_id,
            "我要上架一款成本95元的无线耳机，售价300元，库存800件，毛利率不低于40%。",
        )
        _state_b, product_b = _record(
            repository,
            conversation.conversation_id,
            "我要上架一款成本80元的无线耳机，售价260元，库存600件，毛利率不低于35%。",
        )
        ledger = ProductLedger(database)
        resolver = EntityResolver(ledger, repository)

        structural_queries = [
            product_a.product_id,
            product_a.sku or "",
            state_a.task_id,
        ]
        trials = [structural_queries[index % len(structural_queries)] for index in range(100)]
        hits = sum(
            resolver.resolve("tenant_demo", query).product_id == product_a.product_id
            for query in trials
        )
        ambiguous = resolver.resolve("tenant_demo", "无线耳机")
        multi_trials = 20
        multi_clarified = sum(
            resolver.resolve("tenant_demo", "无线耳机").status == "ambiguous"
            for _ in range(multi_trials)
        )
        cross_tenant_hidden = (
            resolver.resolve("tenant_beta", product_a.product_id).status == "not_found"
        )
        ledger.mark_deleted("tenant_demo", product_a.product_id)
        deleted_hidden = (
            resolver.resolve("tenant_demo", product_a.product_id).status == "not_found"
        )
        detail = ledger.detail("tenant_demo", product_b.product_id)

        result = {
            "version": "v30",
            "schema_version": 3,
            "single_candidate": {
                "hits": hits,
                "trials": len(trials),
                "accuracy": hits / len(trials),
                "threshold": 0.98,
            },
            "multiple_candidates": {
                "clarified": multi_clarified,
                "trials": multi_trials,
                "rate": multi_clarified / multi_trials,
                "candidate_count": len(ambiguous.candidates),
            },
            "isolation": {
                "cross_tenant_hidden": cross_tenant_hidden,
                "deleted_hidden": deleted_hidden,
            },
            "relationship_index": {
                "task_linked": ledger.product_for_task("tenant_demo", _state_b.task_id)
                is not None,
                "artifact_refs_linked": bool(detail.task_links[0].artifact_refs),
                "timeline_event_count": len(detail.timeline),
                "seller_snapshot_available": detail.seller_state_available,
            },
        }
        result["passed"] = bool(
            result["single_candidate"]["accuracy"] >= 0.98
            and result["multiple_candidates"]["rate"] == 1.0
            and all(result["isolation"].values())
            and all(result["relationship_index"].values())
        )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "V30_ACCEPTANCE.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORT_DIR / "V30_ACCEPTANCE.md").write_text(
        "# V30 Acceptance\n\n"
        f"- Result: **{'PASS' if result['passed'] else 'FAIL'}**\n"
        f"- Single-candidate accuracy: {result['single_candidate']['accuracy']:.2%}\n"
        f"- Multi-candidate clarification: {result['multiple_candidates']['rate']:.2%}\n"
        f"- Timeline events: {result['relationship_index']['timeline_event_count']}\n"
        f"- Cross-tenant hidden: {result['isolation']['cross_tenant_hidden']}\n"
        f"- Deleted product hidden: {result['isolation']['deleted_hidden']}\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
