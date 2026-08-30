from __future__ import annotations

import os
import tempfile
from pathlib import Path


# Set before test modules import app.config. Runtime artifacts must never appear
# beside an operator's real checkpoints, traces, conversations, or seller runs.
_TEST_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="ecompilot-v59-pytest-"))
os.environ["ECOMPILOT_RUNTIME_DATA_DIR"] = str(_TEST_RUNTIME_DIR)
