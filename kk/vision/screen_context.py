from __future__ import annotations

from typing import Any

from kk.tools.windows.native import foreground_window, monitor_bounds_for_window

from collections import deque
from datetime import datetime, timezone
from threading import Event, RLock, Thread
from time import monotonic
from PIL import ImageChops, ImageStat


class ScreenContextManager:
    """Bounded metadata history + two tiny RAM frames. Never calls an LLM."""
    def __init__(self, capture, uia, retention_seconds=180, interval=1.0):
        self.capture, self.uia = capture, uia
        self.retention_seconds, self.interval = retention_seconds, interval
        self.events = deque(maxlen=300)
        self.frames = deque(maxlen=2)
        self.stop_event = Event()
        self.lock = RLock()
        self.thread = None
        self.last = None
        self.diff_region = None
        self.diff_observed_at = 0.0
        self.last_windows = set()
        self.last_ui = None
        self.last_ui_at = 0

    def _event(self, kind, data):
        self.events.append({'timestamp': datetime.now(timezone.utc).isoformat(),
                            'time': monotonic(), 'event': kind, 'data': data})

    def sample(self):
        from kk.tools.windows.windows import list_visible_windows
        from kk.ui.capture_guard import is_own_window
        context = current_screen_context()
        with self.lock:
            changed = context != self.last
            if changed:
                self._event('foreground_changed', context)
                self.last = context
            windows = {w['hwnd']: w for w in list_visible_windows() if not is_own_window(w['hwnd'])}
            for hwnd in windows.keys() - self.last_windows:
                self._event('window_appeared', windows[hwnd])
            self.last_windows = set(windows)
            # Polling diffs are labelled explicitly; no claim of native UIA events.
            if changed or monotonic() - self.last_ui_at > 5:
                ui = self.uia.tree(interactive_only=True,include_text=False)
                summary = [(e.get('name'), e.get('focused'), e.get('selected'), e.get('toggle_state')) for e in ui]
                if summary != self.last_ui:
                    self._event('uia_poll_changed', {'elements': len(ui),
                        'focused': [e for e in ui if e.get('focused')][:1]})
                    self.last_ui = summary
                self.last_ui_at = monotonic()
            image, metadata = self.capture.capture_image('active_window', max_dimension=160)
            try:
                gray = image.convert('L')
            finally:
                image.close()
            if self.frames and not changed and self.frames[-1].size == gray.size:
                with ImageChops.difference(self.frames[-1], gray) as diff:
                    if ImageStat.Stat(diff).mean[0] > 8:
                        with diff.point(lambda p: 255 if p > 24 else 0) as threshold:
                            box = threshold.getbbox()
                        if box:
                            sx, sy = metadata['scale']
                            left, top, _, _ = metadata['bounds']
                            self.diff_region = (left+int(box[0]/sx), top+int(box[1]/sy),
                                                left+int(box[2]/sx), top+int(box[3]/sy))
                            self.diff_observed_at = monotonic()
                            self._event('screen_changed', {'region': self.diff_region})
            elif changed:
                self.diff_region = None
            if len(self.frames) == self.frames.maxlen:
                self.frames.popleft().close()
            self.frames.append(gray)
            self._prune()

    def recent_diff_region(self, max_age=5.0):
        with self.lock:
            if monotonic() - self.diff_observed_at <= max_age:
                return self.diff_region
            return None

    def _clear_frames(self):
        while self.frames:
            self.frames.popleft().close()

    def _prune(self):
        cutoff = monotonic() - self.retention_seconds
        while self.events and self.events[0]['time'] < cutoff:
            self.events.popleft()

    def recent(self, limit=30):
        with self.lock:
            self._prune()
            return [{k:v for k,v in e.items() if k != 'time'} for e in list(self.events)[-max(1,min(limit,100)):]]

    def reset(self, reason='manual'):
        """Drop HWND/pixel state that is invalid after resume/topology change."""
        with self.lock:
            self._clear_frames()
            self.last = None
            self.last_windows.clear()
            self.last_ui = None
            self.last_ui_at = 0
            self.diff_region = None
            self.diff_observed_at = 0.0
            self._event('context_reset',{'reason':reason})

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        def run():
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                events=None
                try:
                    from .uia_events import UIAFocusEvents
                    def focused(metadata):
                        with self.lock:
                            self._event('uia_focus_changed',metadata)
                            self.last_ui_at=0
                    events=UIAFocusEvents(focused)
                except Exception as exc:
                    with self.lock:
                        self._event('uia_events_unavailable',{'error':str(exc)})
                try:
                    while not self.stop_event.is_set():
                        try:
                            self.sample()
                        except Exception as exc:
                            with self.lock:
                                self._event('observation_unavailable', {'error': type(exc).__name__})
                        self.stop_event.wait(self.interval)
                finally:
                    if events:
                        events.close()
        self.thread = Thread(target=run, name='KKContext', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        with self.lock:
            self._clear_frames()
            self.events.clear()


def current_screen_context() -> dict[str, Any]:
    foreground = foreground_window()
    from kk.ui.capture_guard import is_own_window
    if is_own_window(foreground.get('hwnd')):
        return {'foreground': {}, 'active_monitor_bounds': None, 'excluded_own_window': True}
    bounds = monitor_bounds_for_window(foreground["hwnd"]) if foreground["hwnd"] else None
    return {"foreground": foreground, "active_monitor_bounds": bounds}
