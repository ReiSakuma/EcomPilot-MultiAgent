from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.conversations.repository import ConversationRepository
from app.copilot.events import CopilotEventStore
from app.copilot.facade import ConversationFacade
from app.public_progress import bind_public_event_sink
from app.copilot_ui import COPILOT_HTML
from app.orchestration.workflow import run_workflow


GOAL = (
    "我要上架一款成本95元、售价300元、库存800件的无线耳机，"
    "最低毛利率40%，面向游戏爱好者。"
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ecompilot_v34_") as directory:
        database = Path(directory) / "product_workspace.db"
        repository = ConversationRepository(database)
        conversation = repository.create_conversation("tenant_demo", title="无线耳机")
        repository.set_active_product(
            "tenant_demo", conversation.conversation_id, "product_headphone"
        )
        stream_store = CopilotEventStore(database)
        stream_id = stream_store.create("tenant_demo", conversation.conversation_id)
        active_before_completion = stream_store.active_stream(
            "tenant_demo", conversation.conversation_id
        )

        def publish(event: dict) -> None:
            stream_store.append("tenant_demo", stream_id, **event)

        with bind_public_event_sink(publish):
            response = ConversationFacade.build_response(
                run_workflow(GOAL, approved=False)
            )
        live_events = stream_store.events_after("tenant_demo", stream_id)
        stream_store.complete("tenant_demo", stream_id, response)
        events = stream_store.events_after("tenant_demo", stream_id)
        resumed = stream_store.events_after(
            "tenant_demo", stream_id, events[max(0, len(events) - 2)].event_id
        )

        checks = {
            "protocol_1_6": response.protocol_version == "1.6",
            "approval_hash_bound": bool(
                response.approval_required
                and response.execution_plan_hash
                and len(response.execution_plan_hash) == 64
            ),
            "terminal_event_persisted": bool(
                events and events[-1].event_type == "response_ready"
            ),
            "execution_events_published_live": all(
                event_type in {event.event_type for event in live_events}
                for event_type in {"agent_started", "agent_completed", "tool_completed"}
            ),
            "active_stream_discoverable": bool(
                active_before_completion
                and active_before_completion["stream_id"] == stream_id
                and stream_store.active_stream(
                    "tenant_demo", conversation.conversation_id
                ) is None
            ),
            "sse_resume_cursor": bool(
                resumed and resumed[0].event_id > events[-2].event_id
            ),
            "search_filter": repository.list_conversations(
                "tenant_demo", query="耳机"
            )[0].conversation_id == conversation.conversation_id,
            "product_filter": repository.list_conversations(
                "tenant_demo", product_id="product_headphone"
            )[0].conversation_id == conversation.conversation_id,
            "mobile_dual_view": all(
                marker in COPILOT_HTML
                for marker in ('data-mobile-mode="chat"', 'data-mobile-mode="results"')
            ),
            "user_ui_uses_dispatch_sse": all(
                marker in COPILOT_HTML
                for marker in (
                    "/api/copilot/messages/dispatch", "new EventSource", "/active-stream"
                )
            ),
        }
        report = {
            "version": "v34.1",
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "stream_id": stream_id,
            "event_count": len(events),
            "panel_ids": [panel.panel_id for panel in response.panels],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
