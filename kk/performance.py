from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Iterator


class PerformanceRecorder:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = Lock()

    def record(self, metric: str, seconds: float, **metadata: Any) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metric": metric,
            "seconds": round(seconds, 6),
            **metadata,
        }
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    @contextmanager
    def measure(self, metric: str, **metadata: Any) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self.record(metric, perf_counter() - started, **metadata)

