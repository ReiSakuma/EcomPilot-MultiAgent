from __future__ import annotations

from app.agents.base import Agent
from app.orchestration.handoff import Handoff
from app.orchestration.failures import failure_from_exception
from app.orchestration.state import TaskState
from app.safety.permissions import RiskLevel, classify_operation
from app.seller_center.schemas import ExecutionPlan


class BrowserAgent(Agent):
    name = "browser_agent"

    def run(self, state: TaskState) -> Handoff:
        self.build_context(state)
        review = state.require_agent_output(
            "review_agent", required_keys=("execution_plan",)
        )
        plan = review["execution_plan"]
        parsed_plan = ExecutionPlan.model_validate(plan)
        for agent_name, expected_hash in parsed_plan.source_artifact_hashes.items():
            artifact_id = state.latest_artifacts.get(agent_name)
            artifact = state.artifacts.get(artifact_id or "")
            if artifact is None or artifact.content_hash != expected_hash:
                return Handoff(
                    task_id=state.task_id,
                    source_agent="browser_agent",
                    target_agent="supervisor",
                    status="failed",
                    result={"risk": "high", "execution_plan": plan},
                    confidence=1.0,
                    error=f"execution_source_hash_mismatch:{agent_name}",
                    failure=failure_from_exception(
                        RuntimeError(f"execution_source_hash_mismatch:{agent_name}"),
                        stage="browser",
                        agent_name=self.name,
                        trace_refs=(state.run_id,),
                    ),
                )
        operation = str(plan["operation"])
        risk = classify_operation(operation)
        if risk is RiskLevel.high and not state.approved:
            return Handoff(
                task_id=state.task_id,
                source_agent="browser_agent",
                target_agent="supervisor",
                status="requires_review",
                result={"risk": risk.value, "execution_plan": plan},
                confidence=1.0,
                error="human_approval_required",
            )
        result = self.tools.call(
            "browser_execute", plan=plan, idempotency_key=f"{state.task_id}:{operation}"
        )
        if state.constraints.get("force_execution_verification_failure"):
            plan = ExecutionPlan.model_validate(
                {
                    **plan,
                    "title": f"{plan.get('title')} 验证不匹配",
                    "payload_hash": "",
                }
            ).model_dump(mode="json")
        verification = self.tools.call("browser_verify", plan=plan)
        verified = bool(verification.get("verified"))
        failure = (
            None
            if verified
            else failure_from_exception(
                RuntimeError("execution_verification_failed"),
                stage="browser",
                agent_name=self.name,
                trace_refs=(state.run_id,),
            )
        )
        return Handoff(
            task_id=state.task_id,
            source_agent="browser_agent",
            target_agent="supervisor",
            result={"risk": risk.value, "browser_result": result, "verification": verification},
            confidence=0.95 if verified else 0.55,
            status="completed" if verified else "failed",
            error=None if verified else "execution_verification_failed",
            failure=failure,
        )
