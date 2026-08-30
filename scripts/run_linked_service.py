from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from app.linked_runtime import get_linked_runtime_status


def main() -> None:
    status = get_linked_runtime_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not status["ready"]:
        print(
            "Linked service refused to start. Configure DeepSeek, fail_closed, "
            "Strategy ReAct, Listing/Strategy/Review LLM agents, and Playwright first.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    parsed = urlparse(status["browser"]["base_url"])
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
