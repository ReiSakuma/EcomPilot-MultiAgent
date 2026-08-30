from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.operations.assessment import build_operational_readiness


def main() -> None:
    report = build_operational_readiness(jobs=240, workers=12, persist=True)
    payload = report.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if report.status != "reference_validated":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
