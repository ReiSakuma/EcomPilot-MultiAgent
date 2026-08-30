from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.eval.mvp_gate import run_mvp_gate
from app.release.evidence import build_evidence_manifest, write_evidence_manifest


def main() -> None:
    report = run_mvp_gate(root=ROOT)
    output = ROOT / "reports" / "raw" / "v38_mvp_gate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report["passed"]:
        write_evidence_manifest(build_evidence_manifest())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
