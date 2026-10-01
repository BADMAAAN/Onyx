from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Any, Literal
from contextlib import nullcontext
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from PIL import ImageGrab, Image

from kk.tools.windows.native import foreground_window, monitor_bounds_for_window, window_rect, list_monitors
from .errors import PerceptionError


CaptureTarget = Literal["all", "active_monitor", "active_window"]


@dataclass(slots=True)
class CapturedScreen:
    path: Path
    target: CaptureTarget
    width: int
    height: int
    foreground: dict[str, Any]
    temporary: bool = True
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path), "target": self.target,
            "width": self.width, "height": self.height,
            "foreground": self.foreground, "temporary": self.temporary,
        }

    def cleanup(self) -> None:
        if self.temporary:
            self.path.unlink(missing_ok=True)


class ScreenCapture:
    def __init__(self, keep: bool = False):
        self.keep = keep

    def capture_image(self, target='active_window', region=None, max_dimension=1280, *, include_own_visual=False):
        """RAM-only path. Region is in virtual desktop coordinates, including negatives."""
        if target not in {'active_window', 'active_monitor', 'all', 'cursor'}:
            raise PerceptionError('capture', 'invalid_target', f'Неизвестная цель захвата: {target}')
        if not isinstance(max_dimension, int) or max_dimension < 1:
            raise PerceptionError('capture', 'invalid_size', 'Размер снимка должен быть положительным')
        foreground = foreground_window()
        hwnd = foreground.get('hwnd', 0)
        from kk.ui.capture_guard import clean_capture, is_own_window
        if hwnd and is_own_window(hwnd):
            foreground, hwnd = {}, 0
        monitors = list_monitors()
        if not monitors:
            raise PerceptionError('capture', 'no_monitor', 'Windows не сообщает доступных мониторов')
        desktop = (min(m['left'] for m in monitors), min(m['top'] for m in monitors),
                   max(m['right'] for m in monitors), max(m['bottom'] for m in monitors))
        if region is not None:
            if (not isinstance(region, (list, tuple)) or len(region) != 4 or
                    any(isinstance(v, bool) or not isinstance(v, int) for v in region)):
                raise PerceptionError('capture', 'invalid_region', 'region должен содержать четыре целых координаты [left, top, right, bottom]')
            bounds = tuple(region)
        elif target == 'active_window' and hwnd:
            bounds = window_rect(hwnd)
        elif target == 'active_monitor' and hwnd:
            bounds = monitor_bounds_for_window(hwnd)
        elif target == 'all':
            bounds = desktop
        elif target == 'cursor':
            import ctypes
            from ctypes import wintypes
            point = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            bounds = (point.x-256, point.y-256, point.x+256, point.y+256)
        else:
            primary = next((m for m in monitors if m.get('primary')), monitors[0])
            bounds = tuple(primary[key] for key in ('left', 'top', 'right', 'bottom'))
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise PerceptionError('capture', 'invalid_region', 'Область снимка пуста')
        # Explicit crops must never silently become a different screen. Window
        # shadows/cursor crops may extend beyond the desktop and can be clipped.
        clipped = (max(bounds[0], desktop[0]), max(bounds[1], desktop[1]),
                   min(bounds[2], desktop[2]), min(bounds[3], desktop[3]))
        if region is not None and clipped != bounds:
            raise PerceptionError('capture', 'outside_desktop', 'Заданная область выходит за доступные мониторы')
        bounds = clipped
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise PerceptionError('capture', 'outside_desktop', 'Окно находится за пределами доступного экрана')
        try:
            with (nullcontext() if include_own_visual else clean_capture()):
                captured_at = monotonic()
                timestamp = datetime.now(timezone.utc).isoformat()
                image = ImageGrab.grab(bbox=bounds, all_screens=True)
        except Exception as exc:
            if 'capture/exclusion_failed' in str(exc):
                raise PerceptionError('capture', 'exclusion_failed',
                                      'Свечение не подтвердило скрытие перед снимком') from exc
            raise PerceptionError('capture', 'backend_failed', 'Не удалось получить чистый кадр рабочего стола') from exc
        try:
            if image.width < 1 or image.height < 1:
                raise PerceptionError('capture', 'empty_frame', 'Захват вернул пустой кадр')
            if image.mode != 'RGB':
                converted = image.convert('RGB')
                image.close()
                image = converted
            if all(high == 0 for _low, high in image.getextrema()):
                raise PerceptionError('capture', 'blank_or_protected',
                                      'Получен полностью чёрный кадр; содержимое не удалось подтвердить')
            if target == 'active_window' and region is None and hwnd:
                after = foreground_window()
                if (after.get('hwnd'), after.get('process_id')) != (hwnd, foreground.get('process_id')):
                    raise PerceptionError('capture', 'target_changed', 'Активное окно сменилось во время захвата')
            original = image.size
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            selected = [m for m in monitors if bounds[0] < m['right'] and bounds[2] > m['left']
                        and bounds[1] < m['bottom'] and bounds[3] > m['top']]
            return image, {'foreground': foreground, 'bounds': bounds, 'original_size': original,
                'scale': (image.width/original[0], image.height/original[1]),
                'id': uuid4().hex, 'timestamp': timestamp, 'captured_monotonic': captured_at,
                'monitor': selected[0] if len(selected) == 1 else None, 'monitors': selected,
                'coordinate_space': 'physical_virtual_desktop', 'color_mode': image.mode,
                'capture_backend': 'Pillow.ImageGrab', 'own_visual_excluded': not include_own_visual}
        except BaseException:
            image.close()
            raise

    def capture(self, target: CaptureTarget = "all") -> CapturedScreen:
        image, metadata = self.capture_image(target, max_dimension=32768)
        folder = Path(tempfile.gettempdir()) / "KK"
        folder.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix="screen_", suffix=".png", dir=folder, delete=False)
        handle.close()
        path = Path(handle.name)
        try:
            image.save(path, "PNG", optimize=True)
            return CapturedScreen(path, target, image.width, image.height, metadata['foreground'], not self.keep, metadata)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            image.close()
