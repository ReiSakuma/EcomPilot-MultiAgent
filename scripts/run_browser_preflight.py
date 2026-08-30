from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.browser.runtime import get_browser_runtime_status


def main() -> None:
    status = get_browser_runtime_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not status["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
