from __future__ import annotations

import ctypes
from time import sleep
from typing import Any


BUTTONS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


def move_mouse(x: int, y: int):
    from .native import list_monitors
    if not any(m['left'] <= x < m['right'] and m['top'] <= y < m['bottom'] for m in list_monitors()):
        raise ValueError('Coordinates are outside connected displays')
    if not ctypes.windll.user32.SetCursorPos(x, y): raise OSError('SetCursorPos failed')
    return {'position': [x, y]}


def click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    width = ctypes.windll.user32.GetSystemMetrics(78)
    height = ctypes.windll.user32.GetSystemMetrics(79)
    left = ctypes.windll.user32.GetSystemMetrics(76)
    top = ctypes.windll.user32.GetSystemMetrics(77)
    if not (left <= x < left + width and top <= y < top + height):
        raise ValueError("Координаты находятся вне виртуального рабочего стола")
    events = BUTTONS.get(button.casefold())
    if not events:
        raise ValueError("button должен быть left, right или middle")
    if not ctypes.windll.user32.SetCursorPos(x, y):
        raise OSError("SetCursorPos failed")
    ctypes.windll.user32.mouse_event(events[0], 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(events[1], 0, 0, 0, 0)
    return {"clicked": [x, y], "button": button}


def scroll(amount: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    if not -20 <= amount <= 20 or amount == 0:
        raise ValueError("amount должен быть от -20 до 20 и не равен нулю")
    if x is not None and y is not None:
        ctypes.windll.user32.SetCursorPos(x, y)
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, ctypes.c_int(amount * 120).value, 0)
    sleep(0.15)
    return {"scrolled_notches": amount, "verified_foreground_unchanged": True}


def double_click(x, y, button='left'):
    click(x, y, button)
    sleep(.05)
    click(x, y, button)
    return {'double_clicked': [x, y], 'button': button}


def drag(start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int = 500) -> dict[str, Any]:
    duration_ms = max(100, min(duration_ms, 5000))
    steps = max(5, min(100, duration_ms // 20))
    ctypes.windll.user32.SetCursorPos(start_x, start_y)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    try:
        for index in range(1, steps + 1):
            ratio = index / steps
            ctypes.windll.user32.SetCursorPos(
                round(start_x + (end_x - start_x) * ratio),
                round(start_y + (end_y - start_y) * ratio),
            )
            sleep(duration_ms / steps / 1000)
    finally:
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    return {"dragged": [start_x, start_y, end_x, end_y], "duration_ms": duration_ms}
