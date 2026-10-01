from __future__ import annotations

from time import monotonic, sleep
from typing import Any

from .windows import list_visible_windows


def wait_for_window(title: str, timeout_seconds: float = 10) -> dict[str, Any]:
    timeout_seconds = max(0.1, min(float(timeout_seconds), 30.0))
    deadline = monotonic() + timeout_seconds
    needle = title.casefold()
    while monotonic() < deadline:
        matches = [item for item in list_visible_windows() if needle in item["title"].casefold()]
        if matches:
            return {"found": True, "window": matches[0], "matches": len(matches)}
        sleep(0.2)
    return {"found": False, "title": title, "timeout_seconds": timeout_seconds}

