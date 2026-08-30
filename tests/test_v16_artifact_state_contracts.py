from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.orchestration.artifacts import ListingArtifact, artifact_from_result
from app.orchestration.reducer import (
    ArtifactContractError,
    StateReducer,
    StateVersionConflictError,
)
from app.orchestration.snapshot import StateSnapshot
from app.orchestration.state import TaskNode, TaskState
from app.orchestration.workflow import resume_workflow, run_workflow
from app.safety.approval import Approval


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 199 元，主要面向大学生，"
    "库存 800 件，毛利率不能低于 25%。"
)


def listing_result(title: str = "V16 无线耳机") -> dict:
    return {
        "title": title,
        "keywords": ["无线耳机"],
        "bullets": ["低延迟"],
        "compliance_notes": ["未使用绝对化词"],
        "generation_mode": "deterministic",
    }


def test_v16_artifact_is_typed_frozen_and_content_addressed():
    first = artifact_from_result(
        task_id="task_v16",
        producer="listing_agent",
        result=listing_result(),
        input_state_version=2,
        confidence=0.9,
    )
    second = artifact_from_result(
        task_id="task_v16",
        producer="listing_agent",
        result=listing_result(),
        input_state_version=2,
        confidence=0.9,
    )
    changed = artifact_from_result(
        task_id="task_v16",
        producer="listing_agent",
        result=listing_result("修改后的标题"),
        input_state_version=2,
        confidence=0.9,
    )

    assert isinstance(first, ListingArtifact)
    assert first.schema_version == "1.0"
    assert first.content_hash == second.content_hash
    assert first.artifact_id != second.artifact_id
    assert first.content_hash != changed.content_hash
    with pytest.raises(ValidationError):
        first.title = "禁止原地修改"


def test_v16_snapshot_is_detached_and_read_only():
    state = TaskState(goal=GOAL, constraints={"inventory": 800})
    state.nodes = {
        "market": TaskNode(node_id="market", agent_name="market_agent")
    }
    snapshot = StateSnapshot.capture(state)
    state.constraints["inventory"] = 0

    assert snapshot.constraints["inventory"] == 800
    with pytest.raises(TypeError):
        snapshot.constraints["inventory"] = 1
    with pytest.raises(ValidationError):
        snapshot.goal = "禁止修改快照"


def test_v16_workflow_persists_typed_artifacts_without_changing_legacy_outputs():
    state = run_workflow(GOAL, approved=False)

    assert state.status == "waiting_for_approval"
    assert state.state_version == 5
    assert set(state.latest_artifacts) == {
        "market_agent",
        "market_price_gate_agent",
        "listing_agent",
        "strategy_agent",
        "review_agent",
        "browser_agent",
    }
    assert len(state.artifacts) == 6
    assert all(handoff.artifact is not None for handoff in state.handoffs)
    for agent_name, artifact_id in state.latest_artifacts.items():
        artifact = state.artifacts[artifact_id]
        assert artifact.producer == agent_name
        assert artifact.legacy_result() == state.agent_outputs[agent_name]


def test_v16_parallel_agents_are_merged_from_the_same_state_version():
    state = run_workflow(GOAL, approved=False)
    listing = state.artifacts[state.latest_artifacts["listing_agent"]]
    strategy = state.artifacts[state.latest_artifacts["strategy_agent"]]

    assert listing.input_state_version == strategy.input_state_version == 2
    assert listing.content_hash != strategy.content_hash
    assert state.context_usage["listing_agent"]["token_estimate"] > 0
    assert state.context_usage["strategy_agent"]["token_estimate"] > 0


def test_v16_approval_resume_preserves_upstream_artifacts_and_replaces_browser():
    initial = run_workflow(GOAL, approved=False)
    before = deepcopy(initial.latest_artifacts)
    resumed = resume_workflow(
        initial.task_id,
        approval=Approval(approved=True, approver="v16-test"),
        expected_checkpoint_version=initial.checkpoint_version,
    )

    assert resumed.status == "completed"
    for agent_name in [
        "market_agent",
        "market_price_gate_agent",
        "listing_agent",
        "strategy_agent",
        "review_agent",
    ]:
        assert resumed.latest_artifacts[agent_name] == before[agent_name]
    assert resumed.latest_artifacts["browser_agent"] != before["browser_agent"]
    assert before["browser_agent"] not in resumed.artifacts
    assert len(resumed.artifacts) == 6


def test_v16_reducer_rejects_stale_or_tampered_handoff():
    state = TaskState(goal=GOAL, state_version=3)
    node = TaskNode(node_id="listing", agent_name="listing_agent")
    artifact = artifact_from_result(
        task_id=state.task_id,
        producer="listing_agent",
        result=listing_result(),
        input_state_version=3,
        confidence=0.9,
    )
    from app.orchestration.handoff import Handoff

    handoff = Handoff(
        task_id=state.task_id,
        source_agent="listing_agent",
        target_agent="review_agent",
        result=listing_result(),
        artifact=artifact,
    )

    with pytest.raises(StateVersionConflictError):
        StateReducer().apply_handoff(
            state, node, handoff, expected_state_version=2
        )

    tampered = handoff.model_copy(update={"result": listing_result("被篡改")})
    with pytest.raises(ArtifactContractError):
        StateReducer().apply_handoff(
            state, node, tampered, expected_state_version=3
        )
