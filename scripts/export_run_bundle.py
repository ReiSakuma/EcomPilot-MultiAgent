from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import BROWSER_BASE_URL, CHECKPOINT_DIR, TRACE_DIR  # noqa: E402
from app.conversations.repository import (  # noqa: E402
    ConversationNotFoundError,
    ConversationRepository,
)
from app.memory.conversation import ConversationMemoryService  # noqa: E402
from app.observability.store import TraceNotFoundError, TraceStore  # noqa: E402
from app.orchestration.a2a_inspection import (  # noqa: E402
    build_task_collaboration_summary,
)
from app.orchestration.checkpoint import CheckpointStore  # noqa: E402
from app.orchestration.state import TaskState  # noqa: E402
from app.products.ledger import ProductLedger, ProductNotFoundError  # noqa: E402
from app.release.protocols import build_protocol_manifest  # noqa: E402
from app.distributed.runtime import DistributedRuntime  # noqa: E402
from app.operations.assessment import load_operational_report  # noqa: E402
from app.model.contracts import PROMOTION_PROTOCOL_VERSION  # noqa: E402
from app.operations.terminal import project_terminal_outcome  # noqa: E402
from app.reliability.dead_letter import get_dead_letter_store  # noqa: E402
from app.security.ledger import build_task_security_summary  # noqa: E402
from app.release.v59 import build_linkage_identity, build_route_evidence  # noqa: E402


SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "llm_api_key",
    "openai_api_key",
    "deepseek_api_key",
    "password",
    "secret",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export one EcomPilot task and its run evidence as a single ZIP file."
    )
    parser.add_argument(
        "--task-id",
        help="Task to export. Omit to export the newest checkpoint.",
    )
    parser.add_argument(
        "--base-url",
        default=BROWSER_BASE_URL,
        help=f"Running linked-service URL (default: {BROWSER_BASE_URL}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output ZIP path. Defaults to reports/run_bundles/<task>_<run>.zip.",
    )
    parser.add_argument(
        "--token",
        default="demo-merchant-a",
        help="Demo bearer token used only for local evidence APIs.",
    )
    return parser.parse_args()


def fetch_json(
    base_url: str,
    path: str,
    *,
    token: str,
    timeout: float = 3.0,
) -> Any:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return {"collection_status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def is_unavailable(value: Any) -> bool:
    return isinstance(value, dict) and value.get("collection_status") == "unavailable"


def sanitize(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS or lowered.endswith(("_api_key", "_password", "_credential")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): sanitize(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item) for item in value]
    if hasattr(value, "model_dump"):
        return sanitize(value.model_dump(mode="json"))
    return value


def load_task(
    task_id: str | None,
    *,
    base_url: str,
    token: str,
    service_online: bool,
) -> TaskState:
    store = CheckpointStore()
    selected = task_id
    if selected is None and service_online:
        latest = fetch_json(base_url, "/tasks/checkpoints?limit=1", token=token)
        if isinstance(latest, list) and latest:
            selected = latest[0].get("task_id")
    if selected is None:
        checkpoints = store.list(limit=1)
        if not checkpoints:
            raise SystemExit("No task checkpoints were found.")
        selected = str(checkpoints[0]["task_id"])
    if service_online:
        payload = fetch_json(base_url, f"/tasks/{selected}", token=token)
        if not is_unavailable(payload):
            return TaskState.model_validate(payload)
    return store.load(selected)


def collect_trace_chain(run_id: str) -> list[dict[str, Any]]:
    store = TraceStore()
    chain: list[dict[str, Any]] = []
    current: str | None = run_id
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        try:
            trace = store.get_run(current)
        except TraceNotFoundError:
            chain.append({"run_id": current, "collection_status": "trace_not_found"})
            break
        chain.append(trace)
        current = trace["summary"].get("parent_run_id")
    return chain


def result(status: str, evidence: str) -> dict[str, str]:
    return {"status": status, "evidence": evidence}


def build_verification_matrix(
    state: dict[str, Any],
    trace_chain: list[dict[str, Any]],
    security: dict[str, Any],
) -> dict[str, dict[str, str]]:
    models = state.get("model_records") or []
    tools = state.get("tool_records") or []
    nodes = state.get("nodes") or {}
    sql_calls = [item for item in tools if item.get("tool_name") == "query_market_database"]
    browser_calls = [item for item in tools if item.get("tool_name") == "browser_execute"]
    model_tool_calls = sum(len(item.get("tool_calls") or []) for item in models)
    sql_policies = [
        item.get("result_summary", {}).get("policy", {})
        for item in sql_calls
        if item.get("result_summary", {}).get("policy")
    ]
    sandboxes = [
        item.get("result_summary", {}).get("sandbox", {})
        for item in sql_calls
        if item.get("result_summary", {}).get("sandbox")
    ]
    trace_events = [
        event
        for trace in trace_chain
        for event in trace.get("events", [])
    ]
    context_budget_events = [
        event
        for event in trace_events
        if event.get("event_type") == "react_context_budget"
    ]
    review = state.get("agent_outputs", {}).get("review_agent", {})
    strategy = state.get("agent_outputs", {}).get("strategy_agent", {})
    browser = state.get("agent_outputs", {}).get("browser_agent", {})
    security_summary = security.get("summary", {})

    real_model = bool(models) and all(
        item.get("provider") not in {None, "deterministic"}
        and item.get("usage_source") == "actual"
        for item in models
    )
    sql_governed = bool(sql_policies) and all(
        policy.get("status") == "allowed"
        and policy.get("read_only_connection") is True
        and policy.get("row_filter_applied") is True
        for policy in sql_policies
    )
    sandboxed = bool(sandboxes) and all(
        box.get("isolation", {}).get("separate_process") is True
        and box.get("isolation", {}).get("secret_environment_present") is False
        for box in sandboxes
    )
    no_loop_failure = not any(
        event.get("step") == "loop_detection" and event.get("status") == "failed"
        for event in trace_events
    )
    waiting = state.get("status") == "waiting_for_approval"
    browser_status = (nodes.get("browser") or {}).get("status")
    approval_gate = (
        waiting and browser_status == "skipped" and not state.get("approved")
    ) or bool(browser_calls)
    verified = browser.get("verification", {}).get("verified") is True
    evidence_plan = strategy.get("evidence_plan") or {}
    evidence_ledger = strategy.get("evidence_ledger") or {}
    candidate_budget = strategy.get("candidate_budget") or {}
    logical_budget = candidate_budget.get("logical_model_calls") or {}
    candidate_evaluations = strategy.get("candidate_evaluations") or []
    eligible_candidate_ids = {
        item.get("candidate_id")
        for item in candidate_evaluations
        if item.get("eligible") and item.get("candidate_id")
    }
    selected_candidate_id = strategy.get("selected_candidate_id")
    strategy_stage_usage = (state.get("context_usage") or {}).get(
        "strategy_agent:stage", {}
    )
    source_context_tokens = strategy_stage_usage.get("source_context_tokens") or 0
    stage_context_tokens = strategy_stage_usage.get("stage_context_tokens") or 0
    strategy_context_reduction = (
        1.0 - stage_context_tokens / source_context_tokens
        if source_context_tokens
        else None
    )
    degradations = state.get("degradations") or []
    traceable_degradations = [
        item
        for item in degradations
        if item.get("code")
        and (item.get("stage") or item.get("agent_name"))
        and (item.get("trace_refs") or item.get("developer_message"))
    ]
    planned_evidence = evidence_plan.get("selected_tools") or []
    evidence_plan_governed = (
        len(planned_evidence) <= 2
        and len({item.get("tool_name") for item in planned_evidence})
        == len(planned_evidence)
        and evidence_ledger.get("plan_status")
        in {"completed", "repaired", "degraded"}
    )
    react_context_bounded = all(
        (event.get("details") or {}).get("tokens_after", 0)
        <= (event.get("details") or {}).get("input_budget_tokens", 0)
        and bool((event.get("details") or {}).get("system_prompt_sha256"))
        and bool((event.get("details") or {}).get("user_prompt_sha256"))
        for event in context_budget_events
    )

    return {
        "real_llm": result("pass" if real_model else "not_observed", f"{len(models)} model records"),
        "react_tool_choice": result(
            "pass" if model_tool_calls else "not_observed",
            f"{model_tool_calls} model-authored tool calls",
        ),
        "strategy_optional_evidence": result(
            "pass" if strategy.get("selected_evidence_tools") else "not_observed",
            ", ".join(strategy.get("selected_evidence_tools") or [])
            or "model selected no optional strategy evidence",
        ),
        "strategy_evidence_plan_governed": result(
            "pass" if evidence_plan_governed else "not_observed",
            f"planned={len(planned_evidence)}/2; "
            f"status={evidence_ledger.get('plan_status', 'missing')}",
        ),
        "react_context_budget": result(
            (
                "pass"
                if context_budget_events and react_context_bounded
                else "not_observed"
            ),
            f"{len(context_budget_events)} rolling compression events; "
            f"bounded={react_context_bounded if context_budget_events else 'n/a'}",
        ),
        "strategy_stage_context_projection": result(
            (
                "pass"
                if strategy_context_reduction is not None
                and strategy_context_reduction >= 0.35
                else "not_observed"
            ),
            (
                f"source={source_context_tokens}; stage={stage_context_tokens}; "
                f"reduction={strategy_context_reduction:.2%}"
                if strategy_context_reduction is not None
                else "strategy stage context was not recorded"
            ),
        ),
        "strategy_logical_model_call_budget": result(
            (
                "pass"
                if logical_budget
                and logical_budget.get("hard_limit") == 4
                and logical_budget.get("calls_used", 5) <= 4
                else "not_observed"
            ),
            f"used={logical_budget.get('calls_used', 'n/a')}; "
            f"limit={logical_budget.get('hard_limit', 'n/a')}",
        ),
        "strategy_candidate_finalization": result(
            (
                "pass"
                if selected_candidate_id
                and selected_candidate_id in eligible_candidate_ids
                else "not_observed"
            ),
            f"mode={strategy.get('candidate_selection_mode', 'n/a')}; "
            f"selected={selected_candidate_id or 'n/a'}; "
            f"eligible={len(eligible_candidate_ids)}",
        ),
        "degradation_traceability": result(
            (
                "pass"
                if degradations and len(traceable_degradations) == len(degradations)
                else "not_observed"
            ),
            f"traceable={len(traceable_degradations)}/{len(degradations)}",
        ),
        "sql_policy_and_tenant_filter": result(
            "pass" if sql_governed else "not_observed",
            f"{len(sql_policies)} governed SQL results",
        ),
        "process_sandbox": result(
            "pass" if sandboxed else "not_observed",
            f"{len(sandboxes)} SQL sandbox receipts",
        ),
        "structured_a2a": result(
            "pass" if state.get("a2a_delegations") else "not_observed",
            f"{len(state.get('a2a_delegations') or {})} delegations",
        ),
        "independent_review": result(
            "pass" if review and browser_status in {"skipped", "completed"} else "not_observed",
            f"review node={((nodes.get('review') or {}).get('status'))}",
        ),
        "human_approval_gate": result(
            "pass" if approval_gate else "not_observed",
            f"task={state.get('status')}, browser={browser_status}",
        ),
        "capability_least_privilege": result(
            "pass" if security_summary.get("issued", 0) else "not_observed",
            json.dumps(security_summary, ensure_ascii=False),
        ),
        "dag_loop_control": result(
            "pass" if no_loop_failure else "fail",
            "no loop-detection failure" if no_loop_failure else "loop-detection failure found",
        ),
        "browser_execution_and_verify": result(
            "pass" if verified else ("not_applicable" if waiting else "not_observed"),
            f"{len(browser_calls)} browser writes; verified={verified}",
        ),
    }


def live_sections(base_url: str, token: str, online: bool) -> dict[str, Any]:
    if not online:
        return {
            "collection_status": "service_offline",
            "note": "Run the exporter while run_linked_service.py is running to include in-memory audits and Seller Center state.",
        }
    endpoints = {
        "linked_runtime": "/linked/status",
        "llm_runtime": "/llm/status",
        "browser_runtime": "/browser/status",
        "sql_schema": "/api/sql/schema",
        "sql_audits": "/api/sql/audits?limit=200",
        "sandbox_status": "/api/sandbox/status",
        "access_identity": "/api/access/whoami",
        "access_policy": "/api/access/policy",
        "access_audits": "/api/access/audits?limit=200",
        "execution_status": "/api/execution/status",
        "seller_center": "/seller-center/state",
        "tools": "/tools",
        "a2a_capabilities": "/api/a2a/capabilities",
        "release_readiness": "/api/release/readiness",
        "release_threat_model": "/api/release/threat-model",
        "release_evidence": "/api/release/evidence",
        "release_protocols": "/api/release/protocols",
    }
    return {
        name: fetch_json(base_url, path, token=token)
        for name, path in endpoints.items()
    }


def artifact_paths(value: Any) -> set[Path]:
    paths: set[Path] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"screenshot", "screenshot_path", "artifact_path"} and isinstance(item, str):
                path = Path(item).expanduser()
                if path.is_file():
                    try:
                        path.resolve().relative_to(ROOT.resolve())
                    except ValueError:
                        continue
                    paths.add(path.resolve())
            paths.update(artifact_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(artifact_paths(item))
    return paths


def conversation_evidence(state: TaskState) -> dict[str, Any]:
    if not state.conversation_id:
        return {"collection_status": "not_applicable", "reason": "task_has_no_conversation"}
    repository = ConversationRepository()
    tenant_id = state.principal.tenant_id
    try:
        detail = repository.get_detail(tenant_id, state.conversation_id)
    except ConversationNotFoundError:
        return {"collection_status": "conversation_not_found"}
    memory_service = ConversationMemoryService(repository)
    memory = memory_service.get_summary(tenant_id, state.conversation_id)
    context_seed = memory_service.context_seed(tenant_id, state.conversation_id)
    ledger = ProductLedger(repository.database_path)
    products: list[dict[str, Any]] = []
    for product_id in state.entity_refs:
        try:
            products.append(ledger.detail(tenant_id, product_id).model_dump(mode="json"))
        except ProductNotFoundError:
            products.append({"product_id": product_id, "collection_status": "product_not_found"})
    return {
        "detail": detail.model_dump(mode="json"),
        "latest_response": repository.latest_response_payload(
            tenant_id, state.conversation_id
        ),
        "memory_summary": memory.model_dump(mode="json") if memory else None,
        "context_budget": context_seed.get("context_budget", {}),
        "summary_trust": context_seed.get("summary_trust", {}),
        "context_events": memory_service.list_context_events(
            tenant_id, state.conversation_id
        ),
        "products": products,
    }


def build_archive_manifest(entries: dict[str, bytes]) -> dict[str, Any]:
    return {
        "manifest_version": "1.0",
        "bundle_version": "2.5",
        "algorithm": "sha256",
        "entries": [
            {
                "path": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in sorted(entries.items())
        ],
    }


def terminal_evidence(state: TaskState) -> dict[str, Any]:
    try:
        return project_terminal_outcome(
            state.outcome,
            degradation_refs=tuple(item.code for item in state.degradations),
            failure=state.failure,
        ).model_dump(mode="json")
    except ValueError:
        return {
            "protocol_version": "1.0",
            "terminal_class": None,
            "source_outcome": state.outcome.value,
            "reason": "任务仍在运行，尚未进入五种外部终态。",
        }


def main() -> None:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    health = fetch_json(base_url, "/health", token=args.token, timeout=1.5)
    online = isinstance(health, dict) and health.get("status") == "ok"
    state_model = load_task(
        args.task_id,
        base_url=base_url,
        token=args.token,
        service_online=online,
    )
    state = state_model.model_dump(mode="json")
    trace_chain = collect_trace_chain(state_model.run_id)
    a2a = build_task_collaboration_summary(state_model)
    security = build_task_security_summary(state_model.task_id)
    conversation = conversation_evidence(state_model)
    reliability = {
        "protocol_version": "1.0",
        "retry_budget": state_model.retry_budget.model_dump(mode="json"),
        "execution_receipts": {
            key: value.model_dump(mode="json")
            for key, value in state_model.execution_receipts.items()
        },
        "events": state_model.reliability_events,
        "needs_attention": state_model.needs_attention,
        "dead_letters": [
            item.model_dump(mode="json")
            for item in get_dead_letter_store().list(
                tenant_id=state_model.principal.tenant_id,
                task_id=state_model.task_id,
            )
        ],
    }
    task_live = {}
    if online:
        task_live = {
            "a2a": fetch_json(base_url, f"/api/tasks/{state_model.task_id}/a2a", token=args.token),
            "security": fetch_json(base_url, f"/api/tasks/{state_model.task_id}/security", token=args.token),
        }
    live = live_sections(base_url, args.token, online)
    linkage = build_linkage_identity(
        state_model,
        trace_chain=trace_chain,
        seller_snapshot=(live.get("seller_center") if isinstance(live, dict) else None),
    )
    bundle = sanitize(
        {
            "bundle_version": "2.5",
            "promotion_protocol_version": PROMOTION_PROTOCOL_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "task_id": state_model.task_id,
            "run_id": state_model.run_id,
            "service": {"base_url": base_url, "online": online, "health": health},
            "verification_matrix": build_verification_matrix(state, trace_chain, security),
            "route_evidence": build_route_evidence(state_model),
            "linkage_identity": linkage,
            "task_state": state,
            "terminal_outcome": terminal_evidence(state_model),
            "trace_chain": trace_chain,
            "a2a": a2a,
            "security": security,
            "protocol_manifest": build_protocol_manifest().model_dump(mode="json"),
            "conversation": conversation,
            "reliability": reliability,
            "distributed_runtime": DistributedRuntime().snapshot(
                tenant_id=state_model.principal.tenant_id
            ),
            "operational_readiness": load_operational_report(),
            "task_live_projections": task_live,
            "live_runtime_and_audits": live,
        }
    )

    output = args.output or (
        ROOT
        / "reports"
        / "run_bundles"
        / f"ecompilot_{state_model.task_id}_{state_model.run_id}.zip"
    )
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = CHECKPOINT_DIR / f"{state_model.task_id}.json"

    entries: dict[str, bytes] = {
        "run_bundle.json": (
            json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        "conversation.json": (
            json.dumps(sanitize(conversation), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        "protocol_manifest.json": (
            json.dumps(
                build_protocol_manifest().model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8"),
        "reliability.json": (
            json.dumps(sanitize(reliability), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    }
    if checkpoint_path.is_file():
        entries[f"raw/{checkpoint_path.name}"] = checkpoint_path.read_bytes()
    for trace in trace_chain:
        run_id = trace.get("summary", {}).get("run_id") or trace.get("run_id")
        trace_path = TRACE_DIR / f"{run_id}.jsonl"
        if run_id and trace_path.is_file():
            entries[f"raw/{trace_path.name}"] = trace_path.read_bytes()
    for path in sorted(artifact_paths(state)):
        entries[f"artifacts/{path.name}"] = path.read_bytes()

    manifest = build_archive_manifest(entries)
    entries["bundle_manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")

    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)

    print(
        json.dumps(
            {
                "status": "completed",
                "task_id": state_model.task_id,
                "run_id": state_model.run_id,
                "service_online": online,
                "output": str(output),
                "size_bytes": output.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
