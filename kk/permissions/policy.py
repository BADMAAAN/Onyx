from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from threading import Event
from typing import Callable


class PermissionLevel(IntEnum):
    READ = 1
    ACTION = 2
    DANGEROUS = 3


class PermissionDenied(RuntimeError):
    pass


class EmergencyStop(RuntimeError):
    pass


class PermissionPolicy:
    def __init__(
        self,
        autonomy: PermissionLevel,
        stop_file: Path,
        confirmer: Callable[[str], bool] | None = None,
    ):
        self.autonomy = autonomy
        self.stop_file = stop_file
        self.confirmer = confirmer
        self._stop = Event()

    def trigger_stop(self) -> None:
        self._stop.set()

    def clear_stop(self) -> None:
        self._stop.clear()
        self.stop_file.unlink(missing_ok=True)

    def check_stop(self) -> None:
        if self._stop.is_set() or self.stop_file.exists():
            raise EmergencyStop("Emergency Stop активирован")

    def authorize(self, level: PermissionLevel, summary: str) -> None:
        self.check_stop()
        if level <= self.autonomy:
            return
        if level == PermissionLevel.DANGEROUS and self.confirmer:
            if self.confirmer(summary):
                return
        raise PermissionDenied(f"Операция не разрешена: {summary}")
