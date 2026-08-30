from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.access.models import default_principal
from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.copilot.facade import ConversationFacade
from app.copilot.schemas import CopilotOutcome
from app.copilot_ui import COPILOT_HTML
from app.demo_ui import DEMO_HTML
from app.orchestration.checkpoint import CheckpointStore


GOAL = (
    "我要上架一款成本 95 元的无线耳机，目标售价 300 元，库存 800 件，"
    "主要面向游戏爱好者，毛利率不能低于 40%。"
    "已确认的产品功能：蓝牙5.3、游戏低延迟、长续航、快充、通话降噪。"
    "已确认的产品形态：未确认。"
)


def main() -> int:
    token = uuid4().hex
    facade = ConversationFacade()
    waiting = facade.handle_message(
        GOAL,
        principal=default_principal(),
        client_request_id=f"v55_acceptance_start_{token}",
    )
    prompt = waiting.price_confirmation
    before = CheckpointStore().load(waiting.task_id) if waiting.task_id else None
    checks = {
        "version_is_v55": PROJECT_VERSION == "0.55.0",
        "waiting_is_business_pause": waiting.outcome is CopilotOutcome.waiting_for_input,
        "price_confirmation_contract_present": prompt is not None,
        "three_actions_present": bool(
            prompt
            and {option.action for option in prompt.options}
            == {
                "adopt_suggested_price",
                "keep_original_with_evidence",
                "market_analysis_only",
            }
        ),
        "three_market_layers_visible": bool(
            prompt
            and prompt.core_price_band
            and prompt.adjacent_price_band
            and prompt.full_market_band
        ),
        "dirty_samples_reported": bool(prompt and prompt.excluded_sample_count == 2),
        "listing_waits_for_confirmation": bool(
            before and before.nodes["listing"].status.value == "pending"
        ),
        "strategy_waits_for_confirmation": bool(
            before and before.nodes["strategy"].status.value == "pending"
        ),
        "user_ui_has_three_controls": all(
            marker in COPILOT_HTML
            for marker in ("adoptPriceButton", "keepPriceButton", "marketOnlyButton")
        ),
        "operations_ui_is_read_only": (
            "运维监控台（只读）" in DEMO_HTML
            and "/price-confirmation" not in DEMO_HTML
            and "/approve" not in DEMO_HTML
            and "resumeCurrentTask" not in DEMO_HTML
        ),
        "operations_ui_has_cleaning_audit": all(
            marker in DEMO_HTML
            for marker in ("被排除的脏样本", "保留的极端但可解释样本", "可比层分配审计")
        ),
    }
    report = {
        "version": "v55",
        "project_version": PROJECT_VERSION,
        "passed": all(checks.values()),
        "checks": checks,
        "task_id": waiting.task_id,
        "run_id": waiting.run_id,
        "checkpoint_version": prompt.checkpoint_version if prompt else None,
        "price_confirmation": prompt.model_dump(mode="json") if prompt else None,
        "boundary": (
            "v55 adds user-facing market-price confirmation and read-only operations "
            "evidence. Promotion-unit contracts and Strategy candidate redesign begin in v56."
        ),
    }
    target = PROJECT_ROOT / "reports" / "v55" / "v55_acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
