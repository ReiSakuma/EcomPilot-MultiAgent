from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PROJECT_ROOT, PROJECT_VERSION
from app.agents.supervisor import Supervisor
from app.orchestration.state import TaskState
from app.orchestration.workflow import run_workflow
from app.release.compatibility import diagnose_checkpoint_payload
from app.release.v59 import build_route_evidence


NORMAL = (
    "我要上架一款成本95元的无线耳机，目标售价199元，库存800件，"
    "最低毛利率25%，面向游戏爱好者。已确认功能：蓝牙5.3、游戏低延迟、"
    "长续航、快充、通话降噪。已确认产品形态：入耳式。"
)


def main() -> None:
    output_dir = PROJECT_ROOT / "reports" / "v59"
    bundle_dir = output_dir / "run_bundles"
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    success = run_workflow(NORMAL, approved=True, approved_by="v59_acceptance")
    waiting = run_workflow(NORMAL, approved=False)
    failed = Supervisor().run(
        NORMAL,
        approved=True,
        approved_by="v59_acceptance",
        constraint_overrides={"force_execution_verification_failure": True},
    )
    states = {"success": success, "waiting": waiting, "controlled_failure": failed}

    bundle_results: dict[str, dict] = {}
    for name, state in states.items():
        target = bundle_dir / f"{name}_{state.task_id}_{state.run_id}.zip"
        command = [
            sys.executable,
            "scripts/export_run_bundle.py",
            "--task-id",
            state.task_id,
            "--base-url",
            "http://127.0.0.1:1",
            "--output",
            str(target),
        ]
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        terminal = None
        route_stage_count = 0
        manifest_entries = 0
        if target.exists():
            with ZipFile(target) as archive:
                bundle = json.loads(archive.read("run_bundle.json"))
                manifest = json.loads(archive.read("bundle_manifest.json"))
            terminal = (bundle.get("terminal_outcome") or {}).get("terminal_class")
            route_stage_count = len((bundle.get("route_evidence") or {}).get("stages") or {})
            manifest_entries = len(manifest.get("entries") or [])
        bundle_results[name] = {
            "exit_code": completed.returncode,
            "path": str(target),
            "terminal_class": terminal,
            "route_stage_count": route_stage_count,
            "manifest_entries": manifest_entries,
        }

    current = TaskState(goal="compatibility-current")
    current_diagnostic = diagnose_checkpoint_payload(current.model_dump(mode="json"))
    legacy = current.model_dump(mode="json")
    legacy["schema_version"] = "1.0"
    legacy["agent_outputs"] = {"strategy_agent": {"coupon": 10}}
    legacy_diagnostic = diagnose_checkpoint_payload(legacy)
    invalid_diagnostic = diagnose_checkpoint_payload(
        {"schema_version": "0.1", "task_id": "task_old", "goal": None}
    )

    compatibility_checks = {
        "current_checkpoint": current_diagnostic.status == "compatible",
        "legacy_checkpoint_migrated": legacy_diagnostic.status == "migrated",
        "invalid_checkpoint_actionable": (
            invalid_diagnostic.status == "requires_regeneration"
            and invalid_diagnostic.recovery_action == "regenerate_task_from_conversation"
        ),
    }
    compatibility_report = {
        "version": "v59",
        "passed": all(compatibility_checks.values()),
        "checks": compatibility_checks,
        "diagnostics": [
            current_diagnostic.model_dump(mode="json"),
            legacy_diagnostic.model_dump(mode="json"),
            invalid_diagnostic.model_dump(mode="json"),
        ],
    }
    _write(output_dir / "compatibility.json", compatibility_report)

    terminal_expectations = {
        "success": {"success", "degraded_completed"},
        "waiting": {"waiting_user"},
        "controlled_failure": {"manual_attention"},
    }
    bundle_checks = {
        name: (
            item["exit_code"] == 0
            and item["terminal_class"] in terminal_expectations[name]
            and item["route_stage_count"] == 9
            and item["manifest_entries"] >= 4
        )
        for name, item in bundle_results.items()
    }
    bundle_report = {
        "version": "v59",
        "passed": all(bundle_checks.values()),
        "checks": bundle_checks,
        "bundles": bundle_results,
    }
    _write(output_dir / "run_bundle_acceptance.json", bundle_report)

    route_contract = build_route_evidence(success)
    offline_checks = {
        "project_version": PROJECT_VERSION == "0.59.0",
        "success_terminal": success.status == "completed",
        "waiting_terminal": waiting.status == "waiting_for_approval",
        "controlled_failure_terminal": failed.outcome.value == "technical_failed",
        "route_evidence_contract": len(route_contract["stages"]) == 9,
        "compatibility": compatibility_report["passed"],
        "three_run_bundles": bundle_report["passed"],
    }
    offline_report = {
        "version": "v59",
        "project_version": PROJECT_VERSION,
        "passed": all(offline_checks.values()),
        "checks": offline_checks,
        "tasks": {
            name: {
                "task_id": state.task_id,
                "run_id": state.run_id,
                "checkpoint_version": state.checkpoint_version,
                "status": state.status,
                "outcome": state.outcome.value,
            }
            for name, state in states.items()
        },
    }
    _write(output_dir / "offline_acceptance.json", offline_report)
    print(json.dumps(offline_report, ensure_ascii=False, indent=2))
    if not offline_report["passed"]:
        raise SystemExit(1)


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
