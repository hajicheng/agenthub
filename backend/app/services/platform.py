from __future__ import annotations

import os
import sys
from typing import Literal

Platform = Literal["windows", "darwin", "linux"]

IS_WINDOWS = sys.platform == "win32"


def current_platform() -> Platform:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def evidence_gates_enabled() -> bool:
    val = os.environ.get("AGENTHUB_EVIDENCE_GATES", "").strip().lower()
    return val in ("1", "true", "yes", "on")
