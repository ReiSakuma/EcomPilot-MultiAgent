from __future__ import annotations

import json

from app.agents.base import Agent
from app.context.token_budget import estimate_tokens
from app.model.contracts import CoreReviewOutput
from app.model.prompts import review_prompt
from app.model.structured import parse_json_object
from app.orchestration.handoff import Handoff
from app.orchestration.failures import business_failure, failure_from_exception
from app.orchestration.state import TaskState
from app.products.ledger import stable_product_id
from app.safety.content_revision import (
    REPAIRABLE_CONTENT_ACTIONS,
    is_repairable_finding,
    normalize_repairable_finding,
    strategy_consistency_findings,
    unresolved_listing_semantic_findings,
)
from app.safety.strategy_rendering import (
    STRATEGY_RENDER_VERSION,
    canonical_hash,
    render_authoritative_strategy,
    verify_authoritative_strategy,
)
from app.seller_center.schemas import ExecutionPlan


class ReviewAgent(Agent):
    name = "review_agent"

    def run(self, state: TaskState) -> Handoff:
        self.build_context(state, token_budget=450)
        listing = state.require_agent_output(
            "listing_agent", required_keys=("title", "bullets")
        )
        strategy = state.require_agent_output(
            "strategy_agent",
            required_keys=("price", "coupon", "margin", "inventory_check"),
        )
        if strategy.get("strategy_render_version") == STRATEGY_RENDER_VERSION:
            render_authoritative_strategy(
                strategy,
                state.constraints,
                category=str(state.constraints.get("category") or "商品"),
            )
        min_margin = float(state.constraints.get("min_margin_rate", 0.25))
        margin_rate = float(strategy["margin"]["margin_rate"])
        violations: list[str] = []
        deterministic_findings: list[dict] = []
        if margin_rate < min_margin:
            violations.append("margin_below_minimum")
        if not strategy["inventory_check"]["valid"]:
            violations.append("inventory_shortage")
        if any(term in listing["title"] for term in ["最", "第一", "100%"]):
            claim_text = next(
                term for term in ["第一", "100%", "最"] if term in listing["title"]
            )
            violations.append("absolute_marketing_term")
            deterministic_findings.append(
                {
                    "code": "prohibited_marketing_claim",
                    "severity": "high",
                    "blocking": True,
                    "message": "商品标题包含绝对化营销词，需要删除后重新审核",
                    "source_agent": "listing_agent",
                    "artifact_type": "listing",
                    "field_path": "listing.title",
                    "claim_text": claim_text,
                    "suggested_action": "remove_unconfirmed_claim",
                    "claim_origin": "agent_generated",
                    "user_action_required": False,
                }
            )
        listing_consistency_findings = unresolved_listing_semantic_findings(
            listing,
            confirmed_features=list(state.constraints.get("confirmed_features", [])),
            confirmed_product_form=state.constraints.get("confirmed_product_form"),
        )
        ownership_issues = (
            verify_authoritative_strategy(strategy, state.constraints)
            if strategy.get("strategy_render_version") == STRATEGY_RENDER_VERSION
            else []
        )
        strategy_findings = strategy_consistency_findings(strategy, state.constraints)
        if any(
            item.get("code") == "discount_representation_mismatch"
            for item in strategy_findings
        ):
            violations.append("discount_unit_mismatch")
        deterministic_findings.extend(listing_consistency_findings)
        deterministic_findings.extend(strategy_findings)

        review_payload = self._review_payload(state, listing, strategy)
        state.context_usage[self.name].update(
            {
                "token_estimate": estimate_tokens(
                    json.dumps(review_payload, ensure_ascii=False)
                ),
                "parts": 3,
                "compressed": False,
            }
        )
        generated = self._generate_review(state, review_payload)
        model_findings = self._core_model_findings(generated, listing, strategy)
        findings = self._dedupe_findings(
            [
                *deterministic_findings,
                *self._verify_finding_routes(model_findings, listing, strategy),
            ]
        )
        review_notes = [finding["message"] for finding in findings]
        violations.extend(
            f"llm_review:{finding['code']}"
            for finding in findings
            if finding["blocking"] and finding["severity"] == "high"
        )
        blocking_findings = [
            finding
            for finding in findings
            if finding["blocking"] and finding["severity"] == "high"
        ]
        hard_violations = [
            violation
            for violation in violations
            if not violation.startswith("llm_review:")
            and violation != "absolute_marketing_term"
            and violation != "discount_unit_mismatch"
        ]
        loop = state.workflow_loops.get("compliance_repair") or state.workflow_loops.get(
            "listing_review"
        )
        revision_iteration = loop.iteration if loop else 0
        revision_limit = loop.max_iterations if loop else 2
        revision_targets = sorted(
            {
                str(finding["source_agent"])
                for finding in blocking_findings
                if finding.get("source_agent")
                in {"listing_agent", "strategy_agent"}
            }
        )
        revision_requested = bool(
            blocking_findings
            and not hard_violations
            and all(is_repairable_finding(finding) for finding in blocking_findings)
            and revision_targets
            and revision_iteration < revision_limit
        )
        revision_target = (
            revision_targets[0]
            if revision_requested and len(revision_targets) == 1
            else None
        )
        source_artifact_hashes = {
            agent_name: state.artifacts[artifact_id].content_hash
            for agent_name in ("listing_agent", "strategy_agent")
            if (artifact_id := state.latest_artifacts.get(agent_name))
            and artifact_id in state.artifacts
        }
        execution_plan = {
            "operation": "update_listing",
            "product_id": str(state.constraints.get("product_id") or stable_product_id(state.task_id)),
            "title": listing["title"],
            "bullets": listing["bullets"],
            "price": strategy["price"],
            "stock": state.constraints.get("inventory", 0),
            "coupon": strategy["coupon"],
            "task_id": state.task_id,
            "run_id": state.run_id,
            "checkpoint_version": state.checkpoint_version,
            "source_artifact_hashes": source_artifact_hashes,
        }
        if state.intent == "modify_listing":
            current = dict(state.constraints.get("current_snapshot") or {})
            execution_plan.update(
                {
                    "product_id": str(state.constraints["product_id"]),
                    "title": current.get("title") or execution_plan["title"],
                    "bullets": current.get("bullets") or execution_plan["bullets"],
                    "price": current.get("price", execution_plan["price"]),
                    "stock": current.get("stock", execution_plan["stock"]),
                    "coupon": current.get("coupon", execution_plan["coupon"]),
                }
            )
            field_map = {
                "target_price": "price",
                "inventory": "stock",
                "coupon": "coupon",
                "title": "title",
            }
            for change in state.constraints.get("change_plan", []):
                execution_plan[field_map[str(change["field"])]] = change["new_value"]
        execution_plan = ExecutionPlan.model_validate(execution_plan).model_dump(
            mode="json"
        )
        execution_manifest = {
            "protocol_version": "execution-manifest-v1",
            "payload_hash": execution_plan["payload_hash"],
            "source_artifact_hashes": source_artifact_hashes,
            "business_projection_hash": canonical_hash(
                {
                    key: execution_plan[key]
                    for key in ("title", "bullets", "price", "stock", "coupon")
                }
            ),
        }

        result = {
            "approved_for_execution": not violations,
            "violations": violations,
            "review_notes": review_notes,
            "review_findings": findings,
            "revision_requested": revision_requested,
            "revision_target": revision_target,
            "generation_mode": "llm" if generated else self.deterministic_mode(state),
            "consistency_checks": [
                {
                    "check": "listing_confirmed_facts",
                    "status": "passed" if not listing_consistency_findings else "failed",
                    "finding_count": len(listing_consistency_findings),
                    "evidence_refs": [
                        "task.constraints.confirmed_features",
                        "task.constraints.confirmed_product_form",
                    ],
                },
                {
                    "check": "strategy_verified_numbers",
                    "status": "passed" if not strategy_findings and not ownership_issues else "failed",
                    "finding_count": len(strategy_findings) + len(ownership_issues),
                    "ownership_issues": ownership_issues,
                    "evidence_refs": [
                        "strategy.margin",
                        "strategy.inventory_check",
                        "task.constraints",
                    ],
                },
                {
                    "check": "execution_projection",
                    "status": "passed",
                    "finding_count": 0,
                    "payload_hash": execution_plan["payload_hash"],
                    "evidence_refs": list(source_artifact_hashes.values()),
                },
                {
                    "check": "upstream_correction_audit",
                    "status": "passed",
                    "finding_count": 0,
                    "correction_count": len(
                        list(listing.get("semantic_corrections") or [])
                        + list(strategy.get("semantic_corrections") or [])
                    ),
                    "evidence_refs": [
                        "listing.semantic_corrections",
                        "strategy.semantic_corrections",
                    ],
                },
            ],
            "correction_audit": [
                *list(listing.get("semantic_corrections") or []),
                *list(strategy.get("semantic_corrections") or []),
            ],
            "execution_plan": execution_plan,
            "execution_manifest": execution_manifest,
        }
        if revision_requested:
            result["revision_targets"] = revision_targets
        terminal_rejection = bool(violations and not revision_requested)
        failure = None
        if terminal_rejection:
            first_code = str(violations[0])
            matched_finding = next(
                (
                    finding
                    for finding in blocking_findings
                    if first_code
                    in {str(finding.get("code")), f"llm_review:{finding.get('code')}"}
                ),
                None,
            )
            first_message = (
                str(matched_finding.get("message"))
                if matched_finding
                else {
                    "margin_below_minimum": "预计毛利率低于你的最低要求。",
                    "inventory_shortage": "计划投入数量超过了当前库存。",
                }.get(first_code, "当前方案没有通过执行前审核。")
            )
            failure = business_failure(
                code=first_code,
                stage="review",
                user_message=first_message,
                developer_message=", ".join(violations),
            )
        return Handoff(
            task_id=state.task_id,
            source_agent="review_agent",
            target_agent=(
                (revision_target or "supervisor")
                if revision_requested
                else "browser_agent"
            ),
            status=(
                "requires_revision"
                if revision_requested
                else ("completed" if not violations else "failed")
            ),
            result=result,
            confidence=0.9 if not violations else 0.5,
            error=(
                None
                if revision_requested
                else (", ".join(violations) if violations else None)
            ),
            failure=failure,
        )

    @staticmethod
    def _dedupe_findings(findings: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[tuple] = set()
        for finding in findings:
            key = (
                finding.get("code"),
                finding.get("source_agent"),
                finding.get("field_path"),
                finding.get("claim_text"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(finding)
        return deduped

    @staticmethod
    def _verify_finding_routes(
        findings: list[dict], listing: dict, strategy: dict
    ) -> list[dict]:
        routed: list[dict] = []
        content_fields = {
            "listing.title": str(listing.get("title") or ""),
            "listing.keywords": "\n".join(
                str(item) for item in listing.get("keywords", [])
            ),
            "listing.bullets": "\n".join(
                str(item) for item in listing.get("bullets", [])
            ),
            "strategy.launch_plan": str(strategy.get("launch_plan") or ""),
            "strategy.strategy_rationale": str(
                strategy.get("strategy_rationale") or ""
            ),
        }
        for finding in findings:
            normalized = dict(finding)
            claim = str(normalized.get("claim_text") or "").strip()
            if normalized.get("suggested_action") in REPAIRABLE_CONTENT_ACTIONS:
                locations = [
                    path
                    for path, value in content_fields.items()
                    if claim and claim in value
                ]
                declared_path = normalized.get("field_path")
                if len(locations) == 1 and declared_path != locations[0]:
                    actual_path = locations[0]
                    normalized.update(
                        {
                            "source_agent": (
                                "listing_agent"
                                if actual_path.startswith("listing.")
                                else "strategy_agent"
                            ),
                            "artifact_type": (
                                "listing"
                                if actual_path.startswith("listing.")
                                else "strategy"
                            ),
                            "field_path": actual_path,
                        }
                    )
            routed.append(normalize_repairable_finding(normalized))
        return routed

    def _generate_review(self, state: TaskState, payload: dict) -> CoreReviewOutput | None:
        if not self.llm_enabled():
            return None
        schema = CoreReviewOutput.model_json_schema()
        try:
            response = self._call_model(
                state,
                review_prompt(payload),
                schema,
                "review_semantic_check",
                max_output_tokens=900,
            )
            output = CoreReviewOutput.model_validate(parse_json_object(response.text))
            self._mark_structured_valid(state, response.call_id)
            return output
        except Exception as exc:
            state.degradations.append(
                failure_from_exception(
                    exc,
                    stage="review_semantic_optional",
                    agent_name=self.name,
                    trace_refs=(state.run_id,),
                )
            )
            state.model_fallbacks.append(
                {
                    "agent_name": self.name,
                    "purpose": "review_semantic_check",
                    "provider": self.model_adapter.provider,
                    "model": self.model_adapter.model,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "fallback": "deterministic_review",
                }
            )
            return None

    @staticmethod
    def _core_model_findings(
        generated: CoreReviewOutput | None, listing: dict, strategy: dict
    ) -> list[dict]:
        if generated is None:
            return []
        fields = {
            "listing.title": str(listing.get("title") or ""),
            "listing.keywords": "\n".join(str(item) for item in listing.get("keywords", [])),
            "listing.bullets": "\n".join(str(item) for item in listing.get("bullets", [])),
            "strategy.launch_plan": str(strategy.get("launch_plan") or ""),
            "strategy.strategy_rationale": str(strategy.get("strategy_rationale") or ""),
        }
        findings: list[dict] = []
        for issue in generated.issues:
            field_value = fields.get(issue.field_path, "")
            if issue.claim_text not in field_value:
                continue
            source_agent = (
                "listing_agent"
                if issue.field_path.startswith("listing.")
                else "strategy_agent"
            )
            findings.append(
                {
                    "code": issue.code,
                    # The review model evaluates agent-generated artifacts. It may
                    # request a bounded content repair, but an untrusted semantic
                    # opinion cannot independently create a hard execution stop.
                    "severity": "medium" if issue.code == "execution_risk" else "high",
                    "blocking": issue.code != "execution_risk",
                    "message": issue.message,
                    "source_agent": source_agent,
                    "artifact_type": "listing" if source_agent == "listing_agent" else "strategy",
                    "field_path": issue.field_path,
                    "claim_text": issue.claim_text,
                    "suggested_action": (
                        "manual_review"
                        if issue.code == "execution_risk"
                        else "remove_unconfirmed_claim"
                    ),
                    "claim_origin": "agent_generated",
                    "user_action_required": False,
                }
            )
        return findings

    @staticmethod
    def _review_payload(state: TaskState, listing: dict, strategy: dict) -> dict:
        return {
            "goal": state.goal,
            "constraints": {
                key: state.constraints.get(key)
                for key in (
                    "category",
                    "cost",
                    "target_price",
                    "inventory",
                    "min_margin_rate",
                    "target_audience",
                    "confirmed_features",
                    "confirmed_product_form",
                )
            },
            "listing": {
                "title": listing.get("title"),
                "keywords": listing.get("keywords", []),
                "bullets": listing.get("bullets", []),
                "compliance_notes": listing.get("compliance_notes", []),
                "semantic_corrections": listing.get("semantic_corrections", []),
            },
            "strategy": {
                "price": strategy.get("price"),
                "coupon": strategy.get("coupon"),
                "planned_units": strategy.get("planned_units"),
                "margin": strategy.get("margin"),
                "inventory_check": strategy.get("inventory_check"),
                "launch_plan": strategy.get("launch_plan"),
                "strategy_rationale": strategy.get("strategy_rationale"),
                "semantic_corrections": strategy.get("semantic_corrections", []),
            },
        }
