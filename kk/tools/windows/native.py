from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
from typing import Any


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def _monitor_info(handle: int) -> dict[str, Any]:
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "GetMonitorInfo failed")
    r, work = info.rcMonitor, info.rcWork
    dpi_x = dpi_y = 96
    try:
        shcore = ctypes.windll.shcore
        x, y = wintypes.UINT(), wintypes.UINT()
        if shcore.GetDpiForMonitor(handle, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
            dpi_x, dpi_y = int(x.value), int(y.value)
    except (AttributeError, OSError):
        pass
    return {
        "handle": int(handle),
        "monitor_id": info.szDevice,
        "left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom,
        "width": r.right-r.left, "height": r.bottom-r.top,
        "work_area": [work.left, work.top, work.right, work.bottom],
        "primary": bool(info.dwFlags & 1),
        "dpi": {"x": dpi_x, "y": dpi_y},
        "scale_percent": round(dpi_x / 96 * 100),
    }


def monitor_for_window(hwnd: int) -> dict[str, Any]:
    handle = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    result = _monitor_info(handle)
    for monitor in list_monitors():
        if monitor['handle'] == result['handle']:
            return monitor
    return result


def monitor_for_rect(bounds) -> dict[str, Any]:
    rect = wintypes.RECT(*[int(v) for v in bounds])
    handle = user32.MonitorFromRect(ctypes.byref(rect), 2)
    result = _monitor_info(handle)
    for monitor in list_monitors():
        if monitor['handle'] == result['handle']:
            return monitor
    return result


def process_details(hwnd: int) -> dict[str, Any]:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    executable = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                executable = buffer.value
        finally:
            kernel32.CloseHandle(handle)
    return {"process_id": pid.value, "executable": executable,
            "process": Path(executable).name if executable else ""}


def window_metadata(hwnd: int, title: str | None = None) -> dict[str, Any]:
    if title is None:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value
    left, top, right, bottom = window_rect(hwnd)
    minimized = bool(user32.IsIconic(hwnd))
    restored = [left, top, right, bottom]
    if minimized:
        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT),
                        ('showCmd', wintypes.UINT), ('ptMinPosition', wintypes.POINT),
                        ('ptMaxPosition', wintypes.POINT), ('rcNormalPosition', wintypes.RECT)]
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(placement)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            r = placement.rcNormalPosition
            restored = [r.left, r.top, r.right, r.bottom]
    effective = restored if minimized else [left, top, right, bottom]
    left, top, right, bottom = effective
    monitor = monitor_for_rect(effective)
    dpi = int(user32.GetDpiForWindow(hwnd)) if hasattr(user32, 'GetDpiForWindow') else monitor['dpi']['x']
    return {
        "hwnd": int(hwnd), "title": title, **process_details(hwnd),
        "bounds": effective,
        "raw_bounds": list(window_rect(hwnd)),
        "minimized": minimized,
        "monitor_id": monitor["monitor_id"],
        "monitor_index": monitor.get("index"),
        "local_monitor_bounds": [left-monitor['left'], top-monitor['top'],
                                 right-monitor['left'], bottom-monitor['top']],
        "dpi": dpi, "scale_percent": round(dpi / 96 * 100),
        "virtual_desktop_bounds": virtual_desktop_bounds(),
    }


def foreground_window() -> dict[str, Any]:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"hwnd": 0, "title": "", "process_id": 0, "executable": ""}
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, len(title))
    return window_metadata(hwnd, title.value)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError(ctypes.get_last_error(), "GetWindowRect failed")
    return rect.left, rect.top, rect.right, rect.bottom


def monitor_bounds_for_window(hwnd: int) -> tuple[int, int, int, int]:
    monitor = monitor_for_window(hwnd)
    return monitor['left'], monitor['top'], monitor['right'], monitor['bottom']


def list_monitors() -> list[dict[str, Any]]:
    monitors: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def callback(handle, _hdc, rect_pointer, _data):
        monitors.append(_monitor_info(handle))
        return True

    if not user32.EnumDisplayMonitors(0, None, callback_type(callback), 0):
        raise OSError(ctypes.get_last_error(), "EnumDisplayMonitors failed")
    monitors.sort(key=lambda item: (not item['primary'], item['left'], item['top']))
    for index, monitor in enumerate(monitors, 1):
        monitor['index'] = index
    return monitors


def virtual_desktop_bounds() -> list[int]:
    # SM_X/Y/CX/CYVIRTUALSCREEN. Coordinates may be negative.
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    return [left, top, left+user32.GetSystemMetrics(78), top+user32.GetSystemMetrics(79)]


def list_processes() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
    )
    import csv
    processes = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) >= 5:
            processes.append({"name": row[0], "pid": int(row[1]), "session": row[2], "memory": row[4]})
    return processes


def system_info() -> dict[str, Any]:
    import os
    import platform
    memory = wintypes.MEMORYSTATUSEX() if hasattr(wintypes, "MEMORYSTATUSEX") else None
    # MEMORYSTATUSEX is absent in some Python builds; psutil is intentionally not required.
    return {
        "platform": platform.platform(),
        "windows": platform.win32_ver(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
    }
