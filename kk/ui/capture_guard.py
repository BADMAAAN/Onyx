"""Process-local registration for excluding our renderer from fresh captures."""
from contextlib import contextmanager
from threading import RLock
import ctypes

_lock = RLock()
_owner = None
_windows = {}


def register(owner):
    global _owner
    with _lock:
        _owner = owner


def unregister(owner):
    global _owner
    with _lock:
        if _owner is owner:
            _owner = None
            _windows.clear()


def update_windows(owner, hwnds, pid):
    with _lock:
        if _owner is owner:
            _windows.clear()
            _windows.update({int(hwnd): int(pid) for hwnd in hwnds})


def is_own_window(hwnd):
    """Check current handle ownership; recycled HWNDs are never excluded blindly."""
    with _lock:
        expected = _windows.get(int(hwnd or 0))
    if expected is None:
        return False
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value == expected


@contextmanager
def clean_capture():
    """Only holds the renderer's capture mutex during pixels, never inference.

    WDA success is not proof of backend exclusion. Always use the short hide /
    DwmFlush handshake until a production backend has actually been validated.
    """
    with _lock:
        owner = _owner
    if owner is None:
        yield
    else:
        with owner.capture_guard():
            yield


@contextmanager
def ambient_capture():
    """Background observation must never blink a visible overlay.

    WDA_EXCLUDEFROMCAPTURE is set by the HWND owner but has not been validated
    for every Pillow/driver combination. While a renderer is registered, use
    metadata only. Never substitute stale pixels or mask useful screen edges.
    Explicit task captures still use the bounded composition handshake.
    """
    with _lock:
        owner = _owner
        if owner is not None:
            record = getattr(owner, 'defer_ambient', None)
            if record:
                record()
            yield False
        else:
            # Registration cannot race a background grab and introduce our UI.
            yield True


def diagnostics():
    with _lock:
        owner = _owner
        windows = [{'hwnd': hwnd, 'pid': pid} for hwnd, pid in _windows.items()]
    if owner is None:
        return {'renderer_registered': False, 'ambient_policy': 'pixels_without_renderer'}
    snapshot = getattr(owner, 'diagnostics', lambda: {})()
    return {'renderer_registered': True, 'windows': windows,
            'ambient_policy': 'metadata_only_with_renderer', **snapshot}
