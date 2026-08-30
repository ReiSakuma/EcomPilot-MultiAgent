from __future__ import annotations

from app.agents.base import Agent
from app.model.contracts import ListingModelOutput
from app.model.prompts import listing_prompt
from app.orchestration.handoff import Handoff
from app.orchestration.state import TaskState
from app.safety.content_revision import (
    confirmed_feature_statements,
    findings_for_agent,
    normalize_listing_semantics,
    scrub_listing_result,
)


class ListingAgent(Agent):
    name = "listing_agent"

    def run(self, state: TaskState) -> Handoff:
        context = self.build_context(state)
        market = state.require_agent_output(
            "market_agent", required_keys=("keywords", "sample_size")
        )
        audience = state.constraints.get("target_audience", "年轻用户")
        keywords = market.get("keywords", ["无线耳机"])
        loop = state.workflow_loops.get("compliance_repair") or state.workflow_loops.get(
            "listing_review"
        )
        is_revision = bool(
            loop
            and loop.phase == "revision_pending"
            and (not loop.target_agents or self.name in loop.target_agents)
        )
        revision_feedback = (
            findings_for_agent(list(loop.feedback), self.name)
            if is_revision and loop
            else []
        )
        previous_listing = (
            state.require_agent_output(
                "listing_agent",
                required_keys=("title", "keywords", "bullets", "compliance_notes"),
            )
            if is_revision
            else None
        )
        safe_finalize = bool(is_revision and loop and loop.safe_finalize)
        generated = (
            None
            if safe_finalize
            else self.generate_structured(
                state,
                listing_prompt(
                    context,
                    confirmed_features=list(
                        state.constraints.get("confirmed_features", [])
                    ),
                    confirmed_product_form=state.constraints.get(
                        "confirmed_product_form"
                    ),
                    revision_feedback=revision_feedback,
                    previous_listing=previous_listing,
                ),
                ListingModelOutput,
                "listing_revision" if is_revision else "listing_generation",
            )
        )
        if safe_finalize:
            result = {
                key: list(previous_listing[key])
                if isinstance(previous_listing.get(key), (list, tuple))
                else previous_listing[key]
                for key in ("title", "keywords", "bullets", "compliance_notes")
            }
            result.update(
                {
                    "generation_mode": "safe_revision",
                    "revision_iteration": loop.iteration,
                    "revision_applied_findings": revision_feedback,
                }
            )
            if previous_listing.get("market_evidence_summary") is not None:
                result["market_evidence_summary"] = previous_listing[
                    "market_evidence_summary"
                ]
        elif generated:
            result = {
                "title": generated.title,
                "keywords": generated.keywords,
                "bullets": generated.bullets,
                "compliance_notes": generated.compliance_notes,
                "generation_mode": "llm_revision" if is_revision else "llm",
                "revision_iteration": loop.iteration if is_revision and loop else 0,
                "revision_applied_findings": revision_feedback,
            }
        else:
            confirmed_features = list(
                state.constraints.get("confirmed_features", [])
            )
            confirmed_form = state.constraints.get("confirmed_product_form")
            category = str(state.constraints.get("category") or "商品")
            title = " ".join(
                str(item)
                for item in [confirmed_form, category, *confirmed_features]
                if item
            )[:120]
            if state.constraints.get("force_bad_title"):
                title = f"第一 {title} 100%适合{audience}"
            result = {
                "title": title,
                "keywords": list(
                    dict.fromkeys(
                        [category, *([str(confirmed_form)] if confirmed_form else []), *confirmed_features]
                    )
                )[:8],
                "bullets": confirmed_feature_statements(confirmed_features)
                or ["商品卖点仅采用商家已确认的信息"],
                "compliance_notes": ["未承诺医疗功效", "未使用绝对化营销词", "已参考商家品牌表达记忆"],
                "generation_mode": self.deterministic_mode(state),
                "revision_iteration": loop.iteration if is_revision and loop else 0,
                "revision_applied_findings": revision_feedback,
                "market_evidence_summary": {
                    "competitor_count": market.get("sample_size", {}).get("competitors", 0),
                    "review_count": market.get("sample_size", {}).get("reviews", 0),
                    "top_pain_points": market.get("pain_points", [])[:3],
                },
            }
        if is_revision:
            scrub_listing_result(
                result,
                revision_feedback,
                category=str(state.constraints.get("category") or "商品"),
            )
        normalize_listing_semantics(
            result,
            category=str(state.constraints.get("category") or "商品"),
            confirmed_features=list(state.constraints.get("confirmed_features", [])),
            confirmed_product_form=state.constraints.get("confirmed_product_form"),
        )
        result["content_normalization_version"] = "listing-normalization-v1"
        return Handoff(
            task_id=state.task_id,
            source_agent="listing_agent",
            target_agent="review_agent",
            result=result,
            confidence=0.82,
        )
