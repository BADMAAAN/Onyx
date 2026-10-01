from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
from typing import Any


@dataclass(slots=True)
class ScreenObservation:
    image_path: Path
    application: str
    window_title: str
    resolution: tuple[int, int]
    monitor: dict[str, Any] | None
    mouse_position: tuple[int, int]
    ui_elements: list[dict[str, Any]] = field(default_factory=list)
    image_png: bytes | None = None
    crop_bounds: tuple | None = None
    scale: tuple = (1.0, 1.0)
    id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    captured_monotonic: float = field(default_factory=monotonic)
    hwnd: int = 0
    process_id: int = 0
    cancellation: Any = field(default=None, repr=False)

    def metadata(self) -> dict[str, Any]:
        return {
            'id': self.id, 'timestamp': self.timestamp, 'source': 'vision',
            'application': self.application, 'hwnd': self.hwnd, 'process_id': self.process_id,
            'monitor': self.monitor, 'bounds': self.crop_bounds, 'resolution': self.resolution,
            'scale': self.scale, 'age_seconds': max(0.0, monotonic() - self.captured_monotonic),
            'freshness': 'captured_for_request', 'ui_elements': self.ui_elements,
        }

    def prompt_context(self) -> str:
        ui_summary = self.ui_elements[:80]
        return (
            f"Приложение: {self.application or 'неизвестно'}\n"
            f"Заголовок окна: {self.window_title or 'нет'}\n"
            f"Размер screenshot: {self.resolution[0]}x{self.resolution[1]}\n"
            f"Crop bounds on desktop: {self.crop_bounds}; pixel scale: {self.scale}\n"
            f"Монитор: {self.monitor}\n"
            f"Позиция мыши: {self.mouse_position}\n"
            f"UI Automation элементы: {ui_summary}"
        )


class VisionProvider(ABC):
    @abstractmethod
    def describe_screen(self, observation: ScreenObservation) -> str: ...

    @abstractmethod
    def analyze_window(self, observation: ScreenObservation) -> dict[str, Any]: ...

    @abstractmethod
    def locate_element(self, observation: ScreenObservation, description: str) -> dict[str, Any]: ...

    @abstractmethod
    def answer_about_screen(self, observation: ScreenObservation, question: str) -> str: ...
