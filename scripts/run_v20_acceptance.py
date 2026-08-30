from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TRACE_DIR
from app.orchestration.a2a import CapabilityDirectory
from app.orchestration.a2a_inspection import (
    build_capability_catalog,
    build_task_collaboration_summary,
)
from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval
from app.tools.browser_tools import (
    get_seller_center_snapshot,
    reset_seller_center,
)


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，主要面向大学生，"
    "库存800件，毛利率不能低于25%。"
)


def main() -> None:
    reset_seller_center()
    initial = run_workflow(GOAL, approved=False)
    initial_store = get_seller_center_snapshot()
    initial_records = {
        record.request.capability_id: record
        for record in initial.a2a_delegations.values()
    }
    initial_browser_id = initial.nodes["browser"].delegation_id
    initial_artifacts = dict(initial.latest_artifacts)

    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(
            approved=True,
            approver="v20-acceptance",
            reason="verify A2A approval-resume lineage",
        ),
        expected_checkpoint_version=initial.checkpoint_version,
    )
    resumed_store = get_seller_center_snapshot()
    resumed_browser_id = resumed.nodes["browser"].delegation_id
    assert resumed_browser_id is not None
    resumed_browser = resumed.a2a_delegations[resumed_browser_id]

    market_ref = initial_records["market.research"].output_artifact_ref
    listing_ref = initial_records["listing.compose"].output_artifact_ref
    strategy_ref = initial_records["strategy.plan"].output_artifact_ref
    review_ref = initial_records["risk.review"].output_artifact_ref
    directory = CapabilityDirectory()
    trace_events: list[dict] = []
    for run_id in (initial.run_id, resumed.run_id):
        path = TRACE_DIR / f"{run_id}.jsonl"
        trace_events.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    a2a_trace = [
        event for event in trace_events if event["event_type"] == "a2a_message"
    ]
    serialized_a2a_trace = json.dumps(a2a_trace, ensure_ascii=False)
    catalog = build_capability_catalog(directory)
    collaboration = build_task_collaboration_summary(resumed)
    serialized_collaboration = json.dumps(collaboration, ensure_ascii=False)

    checks = {
        "five_capabilities_discoverable": len(directory.cards()) == 5,
        "initial_dag_created_five_delegations": len(initial_records) == 5
        and all(record.status == "completed" for record in initial_records.values()),
        "parallel_agents_share_market_artifact": initial_records[
            "listing.compose"
        ].request.input_artifact_refs
        == (market_ref,)
        and initial_records["strategy.plan"].request.input_artifact_refs
        == (market_ref,),
        "review_receives_listing_and_strategy_refs": set(
            initial_records["risk.review"].request.input_artifact_refs
        )
        == {listing_ref, strategy_ref},
        "browser_receives_review_ref": initial_records[
            "seller.execute"
        ].request.input_artifact_refs
        == (review_ref,),
        "handoffs_bound_to_delegations": all(
            handoff.delegation_id in initial.a2a_delegations
            and handoff.input_artifact_refs
            == initial.a2a_delegations[
                handoff.delegation_id
            ].request.input_artifact_refs
            for handoff in initial.handoffs
        ),
        "initial_write_blocked_without_approval": initial.status
        == "waiting_for_approval"
        and initial_store["products"] == {},
        "resume_created_child_browser_delegation": resumed_browser_id
        != initial_browser_id
        and resumed_browser.request.parent_delegation_id == initial_browser_id
        and resumed_browser.request.attempt == 2,
        "resume_completed_and_verified_write": resumed.status == "completed"
        and resumed_browser.status == "completed"
        and bool(resumed_store["products"]),
        "upstream_artifacts_preserved": all(
            resumed.latest_artifacts[name] == initial_artifacts[name]
            for name in (
                "market_agent",
                "listing_agent",
                "strategy_agent",
                "review_agent",
            )
        ),
        "a2a_state_machine_is_auditable": len(initial.a2a_events) == 20
        and len(resumed.a2a_events) == 24,
        "a2a_trace_uses_refs_not_business_payloads": len(a2a_trace) == 24
        and "input_artifact_refs" in serialized_a2a_trace
        and "launch_plan" not in serialized_a2a_trace
        and "bullets" not in serialized_a2a_trace,
        "capability_catalog_is_inspectable": catalog["agent_count"] == 5
        and catalog["capability_count"] == 5
        and catalog["state_exchange"] == "artifact_references",
        "task_collaboration_projection_matches_checkpoint": collaboration[
            "summary"
        ]["delegation_count"]
        == 6
        and collaboration["summary"]["transition_count"] == 24
        and collaboration["summary"]["retry_count"] == 1,
        "inspection_projection_hides_business_payloads": "launch_plan"
        not in serialized_collaboration
        and "bullets" not in serialized_collaboration
        and "execution_plan" not in serialized_collaboration,
    }
    report = {
        "version": "v20",
        "passed": all(checks.values()),
        "evidence_mode": "offline_full_dag_a2a_and_resume",
        "live_model_called": False,
        "task_id": resumed.task_id,
        "initial_run_id": initial.run_id,
        "resumed_run_id": resumed.run_id,
        "delegation_count": len(resumed.a2a_delegations),
        "a2a_transition_count": len(resumed.a2a_events),
        "checks": checks,
        "boundary": (
            "V20 implements typed in-process A2A envelopes and deterministic capability routing. "
            "It is not a network A2A service and does not provide cryptographic Agent identity."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
