from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from .native import foreground_window, list_monitors, window_metadata


def list_visible_windows() -> list[dict[str, Any]]:
    user32 = ctypes.windll.user32
    results: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            from kk.ui.capture_guard import is_own_window
            if is_own_window(hwnd): return True
            size = user32.GetWindowTextLengthW(hwnd) + 1
            title = ctypes.create_unicode_buffer(size)
            user32.GetWindowTextW(hwnd, title, size)
            try:
                results.append(window_metadata(int(hwnd), title.value))
            except OSError:
                # A window may disappear between EnumWindows and inspection.
                pass
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return results


def windows_observation():
    """Expose collection limits without changing native window callers."""
    from kk.tools.observation import observation
    return observation(list_visible_windows(), source='Windows EnumWindows + process metadata',
        scope='current desktop: visible titled top-level windows, excluding Onyx renderer',
        completeness='partial', limitations=[
            'Скрытые окна, окна без заголовка и вкладки приложений не перечисляются.',
            'Окна, исчезнувшие во время наблюдения, могут отсутствовать.'])


def find_window_across_monitors(query: str) -> dict[str, Any]:
    needle = query.casefold().strip()
    matches = [w for w in list_visible_windows() if needle in w['title'].casefold()
               or needle in w.get('process','').casefold()]
    matches.sort(key=lambda w: (w.get('minimized',False), w['title'].casefold() != needle))
    return {'query': query, 'matches': matches, 'monitors_checked': list_monitors(),
            'found': bool(matches)}


def move_window_to_monitor(title: str, monitor_index: int) -> dict[str, Any]:
    found = find_window_across_monitors(title)['matches']
    if not found:
        raise LookupError(f'Окно не найдено на виртуальном рабочем столе: {title}')
    if len(found) > 1:
        exact = [w for w in found if w['title'].casefold() == title.casefold()]
        if len(exact) != 1:
            raise LookupError(f'Найдено несколько окон: {[w["title"] for w in found[:5]]}')
        found = exact
    return move_window_handle_to_monitor(found[0]['hwnd'], monitor_index)


def move_window_handle_to_monitor(hwnd: int, monitor_index: int) -> dict[str, Any]:
    window = window_metadata(hwnd)
    if not any(w['hwnd'] == hwnd for w in list_visible_windows()):
        raise LookupError('Target window disappeared')
    monitors = list_monitors()
    target = next((m for m in monitors if m['index'] == int(monitor_index)), None)
    if target is None:
        raise LookupError(f'Монитор {monitor_index} не найден')
    was_maximized=bool(ctypes.windll.user32.IsZoomed(hwnd))
    ctypes.windll.user32.ShowWindow(hwnd, 9)
    window = window_metadata(hwnd)
    left, top, right, bottom = window['bounds']
    width = min(right-left, target['width'])
    height = min(bottom-top, target['height'])
    x = target['left'] + max(0, (target['width']-width)//2)
    y = target['top'] + max(0, (target['height']-height)//2)
    SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
    if not ctypes.windll.user32.SetWindowPos(window['hwnd'], 0, x, y, width, height,
                                             SWP_NOZORDER | SWP_NOACTIVATE):
        raise OSError(ctypes.get_last_error(), 'SetWindowPos failed')
    if was_maximized: ctypes.windll.user32.ShowWindow(hwnd,3)
    moved = window_metadata(window['hwnd'])
    if moved['monitor_index'] != int(monitor_index):
        raise OSError('Window move verification failed')
    return {'moved': True, 'before': window, 'after': moved}


def focus_window(title: str) -> dict[str, Any]:
    needle = title.casefold()
    matches = [w for w in list_visible_windows() if needle in w["title"].casefold()]
    if not matches:
        raise LookupError(f"Окно не найдено: {title}")
    if len(matches) > 1:
        exact = [w for w in matches if w["title"].casefold() == needle]
        if len(exact) == 1:
            matches = exact
        else:
            usable = [w for w in matches if not w.get('minimized')]
            if not usable:
                raise LookupError(f"Найдено несколько свёрнутых окон: {[w['title'] for w in matches[:5]]}")
            # EnumWindows is in Z order: the first usable match is the most
            # recently active instance across the complete virtual desktop.
            matches = usable
    hwnd = matches[0]["hwnd"]
    return focus_window_handle(hwnd)


def focus_window_handle(hwnd: int) -> dict[str, Any]:
    if not any(w['hwnd']==hwnd for w in list_visible_windows()):
        raise LookupError('Target window disappeared')
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    switched = bool(user32.SetForegroundWindow(hwnd))
    if not switched:
        # Windows may deny SetForegroundWindow to a background process. Attach
        # our input queue briefly to the current foreground and target queues;
        # this is the documented desktop-app path and works across monitors.
        foreground = user32.GetForegroundWindow()
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached = []
        try:
            for thread in {foreground_thread, target_thread} - {0, current_thread}:
                if user32.AttachThreadInput(current_thread, thread, True):
                    attached.append(thread)
            user32.BringWindowToTop(hwnd)
            switched = bool(user32.SetForegroundWindow(hwnd))
            user32.SetFocus(hwnd)
        finally:
            for thread in attached:
                user32.AttachThreadInput(current_thread, thread, False)
    if not switched:
        try:
            import uiautomation as auto
            auto.ControlFromHandle(hwnd).SetFocus()
        except Exception as exc:
            raise OSError("Windows не разрешила переключить фокус") from exc
    from time import sleep
    sleep(.2)  # allow DWM foreground animation to settle before pixel observation
    observed = foreground_window()
    if observed.get('hwnd') != hwnd:
        raise OSError('Foreground verification failed')
    return observed
