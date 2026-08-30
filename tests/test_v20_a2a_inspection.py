from __future__ import annotations

import json

from app.demo_ui import DEMO_HTML
from app.main import a2a_capabilities, task_a2a_summary
from app.orchestration.a2a_inspection import (
    build_capability_catalog,
    build_task_collaboration_summary,
)
from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval
from app.tools.browser_tools import reset_seller_center


GOAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，主要面向大学生，"
    "库存800件，毛利率不能低于25%。"
)


def test_capability_catalog_exposes_routes_and_permission_boundaries() -> None:
    catalog = build_capability_catalog()
    routes = {route["capability_id"]: route for route in catalog["routes"]}

    assert catalog["transport"] == "in_process"
    assert catalog["routing_mode"] == "deterministic_capability_dag"
    assert catalog["agent_count"] == 7
    assert routes["analytics.read"]["allowed_tools"] == [
        "get_sales_metrics",
        "compare_sales_periods",
        "get_campaign_performance",
        "get_inventory_history",
    ]
    assert routes["analytics.read"]["read_only"] is True
    assert routes["strategy.plan"]["allowed_tools"] == [
        "suggest_discount",
        "calculate_margin",
        "check_inventory",
        "forecast_demand",
        "query_campaign_history",
        "analyze_competitor_price_trends",
    ]
    assert routes["seller.execute"]["read_only"] is False
    assert a2a_capabilities() == catalog


def test_task_projection_matches_real_checkpoint_and_hides_business_payload() -> None:
    reset_seller_center()
    state = run_workflow(GOAL, approved=False)

    projection = task_a2a_summary(state.task_id)
    serialized = json.dumps(projection, ensure_ascii=False)

    assert projection["task_id"] == state.task_id
    assert projection["run_id"] == state.run_id
    assert projection["summary"]["delegation_count"] == 6
    assert projection["summary"]["transition_count"] == 24
    assert projection["summary"]["artifact_count"] == 6
    assert projection["budget"]["delegations_remaining"] == 6
    assert "launch_plan" not in serialized
    assert "bullets" not in serialized
    assert "execution_plan" not in serialized


def test_task_projection_reconstructs_artifact_lineage_from_references() -> None:
    reset_seller_center()
    state = run_workflow(GOAL, approved=False)
    projection = build_task_collaboration_summary(state)
    delegations = {
        record["capability_id"]: record for record in projection["delegations"]
    }
    artifacts = {
        artifact["artifact_id"]: artifact for artifact in projection["artifacts"]
    }

    market_ref = delegations["market.research"]["output_artifact_ref"]
    review_ref = delegations["risk.review"]["output_artifact_ref"]
    assert set(artifacts[market_ref]["consumer_delegation_ids"]) == {
        delegations["market.price_assess"]["delegation_id"],
        delegations["listing.compose"]["delegation_id"],
        delegations["strategy.plan"]["delegation_id"],
    }
    assert artifacts[review_ref]["consumer_delegation_ids"] == [
        delegations["seller.execute"]["delegation_id"]
    ]
    assert set(artifacts[review_ref]["parent_artifact_refs"]) == {
        delegations["listing.compose"]["output_artifact_ref"],
        delegations["strategy.plan"]["output_artifact_ref"],
    }


def test_resume_projection_keeps_browser_parent_delegation_chain() -> None:
    reset_seller_center()
    initial = run_workflow(GOAL, approved=False)
    first_browser_id = initial.nodes["browser"].delegation_id
    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(approved=True, approver="inspection-test"),
        expected_checkpoint_version=initial.checkpoint_version,
    )
    projection = build_task_collaboration_summary(resumed)
    browser_records = [
        record
        for record in projection["delegations"]
        if record["capability_id"] == "seller.execute"
    ]

    assert len(browser_records) == 2
    assert browser_records[1]["parent_delegation_id"] == first_browser_id
    assert browser_records[1]["attempt"] == 2
    assert projection["summary"]["retry_count"] == 1
    assert projection["budget"]["per_agent_used"]["browser_agent"] == 2


def test_operations_console_contains_live_a2a_collaboration_view() -> None:
    assert "A2A 协作" in DEMO_HTML
    assert "能力目录与权限" in DEMO_HTML
    assert "委派与重试链" in DEMO_HTML
    assert "Artifact 数据血缘" in DEMO_HTML
    assert "/api/a2a/capabilities" in DEMO_HTML
    assert "/a2a'" in DEMO_HTML
    assert "loadA2A(state.task_id)" in DEMO_HTML
