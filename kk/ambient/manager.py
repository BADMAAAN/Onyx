"""Lightweight metadata-first monitor of the complete Windows virtual desktop."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import asdict
from collections import Counter, deque
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from time import monotonic, time

from PIL import ImageGrab, ImageChops, ImageStat

from .model import ActiveUserTask, ContextTimeline, DesktopContextSnapshot, compress_context, previous_window, safe_window, safe_title


def screen_difference(before, after, threshold=12.0, pixel_threshold=25, area_threshold=.08):
    if before is None or before.size != after.size:
        return {'changed': False, 'score': 0.0, 'area': 0.0}
    delta = ImageChops.difference(before, after)
    score = ImageStat.Stat(delta).mean[0]
    mask = delta.point(lambda pixel: 255 if pixel >= pixel_threshold else 0)
    histogram = mask.histogram()
    area = histogram[255] / (after.width * after.height)
    return {'changed': score >= threshold and area >= area_threshold, 'score': round(score, 2), 'area': round(area, 3)}


def idle_seconds():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    tick = ctypes.windll.kernel32.GetTickCount()
    return ((tick - info.dwTime) & 0xffffffff) / 1000


def cursor_position():
    point = wintypes.POINT()
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return (point.x, point.y)
    return None


class AmbientContextManager:
    def __init__(self, database: Path, capture=None, uia=None, retention_seconds=900,
                 interval=2.0, screen_interval=8.0, idle_threshold=180, session_gap=600,
                 excluded_apps=(), persistent_retention_seconds=7*86400):
        self.timeline = ContextTimeline(database, retention_seconds)
        self.capture, self.uia = capture, uia
        self.interval, self.screen_interval = interval, screen_interval
        self.idle_threshold, self.session_gap = idle_threshold, session_gap
        self.excluded_apps = {str(app).casefold() for app in excluded_apps}
        self.excluded_monitors = set()
        self.persistent_retention_seconds = max(60, int(persistent_retention_seconds))
        self.stop_event, self.wake_event = Event(), Event()
        self.lock = RLock()
        self.thread = None
        self.paused = False
        self.windows = {}
        self.foreground = {}
        self.monitors = []
        self.virtual_bounds = []
        self.focused_ui = None
        self.selected_ui = None
        self.last_action = None
        self.user_state = 'unknown'
        self.idle_started_at = None
        self.last_screen_at = 0.0
        self.last_uia_at = 0.0
        self.frames = {}
        self.screen_changes = []
        self.diff_region = None
        self.diff_observed_at = 0.0
        self.samples = 0
        self.sample_errors = 0
        self.uia_errors = 0
        self.screen_errors = 0
        self.screen_samples = 0
        self.screen_samples_by_monitor = {}
        self.screen_deferred = 0
        self.screen_deferred_reason = None
        self.initialized = False
        self.started_at = monotonic()
        self.last_db_prune = time()
        self.last_input_age = 0.0
        self.last_input_at = None
        self.last_cursor = None
        self.active_task = None
        self.ui_event_queue = deque(maxlen=64)
        self.ui_event_lock = Lock()
        self.ui_event_epoch = 0
        self.ui_events_recorded = 0
        self.ui_events_dropped = 0
        self._tool_depth = 0
        self._tool_quiet_until = 0.0
        self._ui_event_times = deque(maxlen=30)
        self._ui_rejections = Counter()
        self._semantic_listener = None
        self._semantic_stage = 'not_started'
        self._semantic_error = None
        self._semantic_apartment = 'not_initialized'

    def _reject_ui_event(self, reason):
        with self.ui_event_lock:
            self._ui_rejections[reason] += 1
            self.ui_events_dropped += 1

    def semantic_diagnostics(self):
        # Numeric/lifecycle metadata only. Reading status performs no UIA calls.
        with self.ui_event_lock:
            result = {'stage': self._semantic_stage, 'apartment': self._semantic_apartment,
                      'last_error': self._semantic_error, 'recorded': self.ui_events_recorded,
                      'dropped': self.ui_events_dropped, 'queue_size': len(self.ui_event_queue),
                      'rejections': dict(self._ui_rejections), 'paused': self.paused,
                      'worker_alive': bool(self.thread and self.thread.is_alive()),
                      'tool_depth': self._tool_depth,
                      'tool_cooldown': monotonic() < self._tool_quiet_until}
        listener = self._semantic_listener
        result['listener'] = listener.diagnostics() if listener else None
        return result

    def _semantic_failure(self, stage, exc):
        self._semantic_stage = 'error'
        self._semantic_error = {'stage': stage, 'type': type(exc).__name__,
                                'hresult': getattr(exc, 'hresult', None)}
        self.uia_errors += 1

    @contextmanager
    def tool_scope(self, name):
        """Exclude our own UI effects from inferred user activity, even on error."""
        with self.lock:
            self._tool_depth += 1
        try:
            yield
        finally:
            with self.lock:
                self._tool_depth = max(0, self._tool_depth - 1)
                self._tool_quiet_until = monotonic() + 1.0

    def enqueue_ui_event(self, metadata):
        # Callback path: bounded cached primitives only, no DB/main lock/UIA read.
        if self.paused or self.stop_event.is_set():
            self._reject_ui_event('paused_or_stopped')
            return
        now = monotonic()
        if self._tool_depth or now < self._tool_quiet_until:
            self._reject_ui_event('onyx_action')
            return
        window = safe_window(metadata.get('window') or {})
        if (metadata.get('protected') or window.get('title') == '[protected]' or self._excluded(window)
                or metadata.get('observation_epoch', self.ui_event_epoch) != self.ui_event_epoch):
            self._reject_ui_event('scope_or_privacy')
            return
        from kk.vision.uia_events import SEMANTIC_CONTROLS
        if metadata.get('control_type') not in SEMANTIC_CONTROLS.values():
            self._reject_ui_event('control_type')
            return
        # Keep labels only; never values, document text, keys or coordinates.
        name = safe_title(metadata.get('name', ''), window.get('process', ''))
        automation_id = safe_title(metadata.get('automation_id', ''), window.get('process', ''))
        if not name or '[protected]' in (name, automation_id):
            self._reject_ui_event('label_privacy')
            return
        item = {key: metadata.get(key) for key in ('operation', 'process_id', 'control_type',
                'uia_event_id', 'timestamp', 'observed_monotonic')}
        item.update(window=window, name=name[:120], automation_id=automation_id[:100],
                    epoch=self.ui_event_epoch, input_age=idle_seconds(),
                    test_automation=(metadata.get('source') == 'test_automation' or
                                     window.get('title') in {'Onyx Interaction Fixture', 'Onyx Interaction Fixture WPF'}))
        with self.ui_event_lock:
            if not self.paused and item['epoch'] == self.ui_event_epoch:
                if len(self.ui_event_queue) == self.ui_event_queue.maxlen:
                    self.ui_events_dropped += 1
                    self._ui_rejections['queue_full'] += 1
                self.ui_event_queue.append(item)
            else:
                self.ui_events_dropped += 1
                self._ui_rejections['enqueue_epoch_or_pause'] += 1
        self.wake_event.set()

    def _drain_ui_events(self, limit=8):
        """Bounded work on the existing ambient worker; persistence rechecks scope."""
        with self.lock:
            for _ in range(min(limit, 8)):
                with self.ui_event_lock:
                    if not self.ui_event_queue:
                        break
                    item = self.ui_event_queue.popleft()
                now = monotonic()
                window = item['window']
                reason = ('paused_or_epoch' if self.paused or self.stop_event.is_set() or item['epoch'] != self.ui_event_epoch
                    else 'onyx_action' if self._tool_depth or now < self._tool_quiet_until
                    else 'expired' if now-item.get('observed_monotonic', 0) > 3
                    else 'no_recent_input' if not 0 <= item.get('input_age', 999) <= 2
                    else 'scope_or_privacy' if self._excluded(window) or window.get('title') == '[protected]' or self.foreground.get('title') == '[protected]'
                    else 'foreground_changed' if (window.get('hwnd'), item.get('process_id')) != (self.foreground.get('hwnd'), self.foreground.get('process_id'))
                    else None)
                if reason:
                    self._reject_ui_event(reason)
                    continue
                operation = item.get('operation')
                if operation not in {'invoke', 'select'}:
                    self._reject_ui_event('operation')
                    continue
                while self._ui_event_times and now-self._ui_event_times[0] >= 60:
                    self._ui_event_times.popleft()
                if len(self._ui_event_times) >= 30:
                    self._reject_ui_event('rate_limit')
                    continue
                details = {'source': 'user_ui', 'attribution': 'inferred',
                    'evidence': 'uia_event + recent_input + no_onyx_action',
                    'confidence': 'medium', 'uia_event_id': item['uia_event_id'],
                    'operation': operation, 'name': item['name'], 'control_type': item['control_type'],
                    'automation_id': item['automation_id'], 'observed_at': item['timestamp']}
                if item.get('test_automation'):
                    details.update(source='test_automation', attribution='explicit_test')
                if self._event('USER_UI_INVOKED' if operation == 'invoke' else 'USER_UI_SELECTED', window, details):
                    self.ui_events_recorded += 1
                    self._ui_event_times.append(now)

    def _event(self, kind, window=None, details=None, persist=True):
        if self.paused or self.stop_event.is_set(): return None
        return self.timeline.add(kind, window, details=details, persist=persist)

    def _excluded(self, window):
        from kk.ui.capture_guard import is_own_window
        return (is_own_window(window.get('hwnd', 0))
                or window.get('title') in {'Onyx Edge Presence','Onyx Orb','KK Edge Presence','KK Orb'}
                or window.get('process','').casefold() in self.excluded_apps)

    def sample(self, *, windows=None, foreground=None, monitors=None, idle=None, now=None, screen=False):
        from kk.tools.windows.native import foreground_window, list_monitors, virtual_desktop_bounds
        from kk.tools.windows.windows import list_visible_windows
        now = time() if now is None else now
        if self.paused:
            return
        windows = list_visible_windows() if windows is None else windows
        foreground = foreground_window() if foreground is None else foreground
        monitors = list_monitors() if monitors is None else monitors
        idle = idle_seconds() if idle is None else idle
        with self.lock:
            if self.paused:
                return
            old_windows = self.windows
            self.excluded_monitors = {w.get('monitor_id') for w in windows
                                      if w.get('process','').casefold() in self.excluded_apps and not w.get('minimized')}
            new_windows = {int(w['hwnd']): safe_window(w) for w in windows if w.get('hwnd') and not self._excluded(w)}
            new_fg = safe_window({} if self._excluded(foreground) else foreground)
            old_pids = {w.get('process_id') for w in old_windows.values()} - {0, None}
            new_pids = {w.get('process_id') for w in new_windows.values()} - {0, None}
            if self.initialized:
                import psutil
                for pid in new_pids - old_pids:
                    try:
                        if now - psutil.Process(pid).create_time() <= max(5, self.interval*2):
                            first = next(w for w in new_windows.values() if w.get('process_id') == pid)
                            self._event('APP_STARTED', first)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            if not self.monitors:
                self.monitors = monitors
            if self.foreground.get('hwnd') != new_fg.get('hwnd') or self.foreground.get('title') != new_fg.get('title'):
                if self.foreground:
                    self.timeline.end_focus()
                if self.foreground.get('hwnd') == new_fg.get('hwnd') and self.foreground:
                    self._event('WINDOW_TITLE_CHANGED', new_fg)
                elif new_fg.get('hwnd'):
                    self._event('WINDOW_FOCUSED', new_fg)
                self.foreground = new_fg
                self.last_uia_at = 0
                process = new_fg.get('process','').casefold()
                if process in {'code.exe','codium.exe','pycharm64.exe','notepad.exe'} and new_fg.get('title') != '[protected]':
                    self.active_task = asdict(ActiveUserTask(new_fg.get('process',''), new_fg.get('title',''), now, now))
            elif self.active_task and self.user_state != 'idle' and self.active_task['application'] == new_fg.get('process'):
                self.active_task['last_activity'] = now
            for hwnd, window in new_windows.items():
                old = old_windows.get(hwnd)
                if old is None:
                    if self.initialized:
                        self._event('WINDOW_OPENED', window)
                else:
                    if old.get('minimized') != window.get('minimized'):
                        self._event('WINDOW_MINIMIZED' if window.get('minimized') else 'WINDOW_RESTORED', window)
                    if old.get('monitor_id') != window.get('monitor_id'):
                        self._event('MONITOR_CHANGED', window, {'from': old.get('monitor_id'), 'to': window.get('monitor_id')})
                    elif old.get('bounds') != window.get('bounds'):
                        self._event('WINDOW_MOVED', window)
                    if old.get('title') != window.get('title') and window.get('title'):
                        self._event('WINDOW_TITLE_CHANGED', window)
            for hwnd in old_windows.keys() - new_windows.keys():
                self._event('WINDOW_CLOSED', old_windows[hwnd])
            if self.initialized:
                import psutil
                for pid in old_pids - new_pids:
                    if not psutil.pid_exists(pid):
                        first = next(w for w in old_windows.values() if w.get('process_id') == pid)
                        self._event('APP_EXITED', first)
            self.windows = new_windows
            self.initialized = True
            if {m['monitor_id'] for m in self.monitors} != {m['monitor_id'] for m in monitors}:
                self.frames.clear()
                self.last_screen_at = 0
            self.monitors = monitors
            self.virtual_bounds = [min(m['left'] for m in monitors), min(m['top'] for m in monitors),
                                   max(m['right'] for m in monitors), max(m['bottom'] for m in monitors)] if monitors else virtual_desktop_bounds()
            self.last_input_age = idle
            input_at = now - idle
            cursor = cursor_position()
            if self.last_input_at is not None and input_at > self.last_input_at + .5:
                source = 'mouse' if cursor is not None and cursor != self.last_cursor else 'keyboard_or_pointer'
                self._event('USER_INPUT', new_fg, {'source': source}, persist=False)
            self.last_input_at, self.last_cursor = input_at, cursor
            new_state = 'idle' if idle >= self.idle_threshold else 'active'
            if new_state != self.user_state:
                if new_state == 'active' and self.user_state == 'idle' and self.idle_started_at is not None and now - self.idle_started_at >= self.session_gap:
                    self.timeline.session_id += 1
                    self._event('SESSION_STARTED', new_fg, {'reason': 'return_from_idle'})
                if new_state == 'active' and self.user_state == 'idle' and new_fg.get('hwnd'):
                    self._event('WINDOW_FOCUSED', new_fg)
                self._event('USER_IDLE' if new_state == 'idle' else 'USER_ACTIVE', new_fg, {'idle_seconds': round(idle)})
                self.user_state = new_state
                if new_state == 'idle':
                    self.timeline.end_focus(at=now-idle)
                    self.idle_started_at = now - idle
                    self.active_task = None
                else:
                    self.idle_started_at = None
            self.samples += 1
            if now - self.last_db_prune > 3600:
                import sqlite3
                try:
                    self.timeline.db.execute('DELETE FROM activity WHERE started < ?', (now - self.persistent_retention_seconds,))
                    self.timeline.db.commit()
                    self.last_db_prune = now
                except sqlite3.OperationalError:
                    self.timeline.db.rollback()
            if screen and monotonic() - self.last_screen_at >= self.screen_interval:
                self._screen_sample(monitors)
                self.last_screen_at = monotonic()
            if self.uia and self.last_uia_at == 0 and new_fg.get('hwnd'):
                self._uia_sample()

    def _uia_sample(self):
        # Targeted UIA read on a foreground transition; never collect edit values.
        if self.paused or self.foreground.get('title') == '[protected]':
            self.focused_ui = self.selected_ui = None
            return
        try:
            elements = self.uia.tree(interactive_only=True, include_text=False)
            focused = next((e for e in elements if e.get('focused') and not e.get('password')), None)
            selected = next((e for e in elements if e.get('selected') and not e.get('password')), None)
            def metadata(e):
                if not e or e.get('control_type') in {'EditControl','ComboBoxControl','DocumentControl'}:
                    return None
                return {'name': safe_title(e.get('name',''), self.foreground.get('process','')),
                        'control_type': e.get('control_type'), 'automation_id': safe_title(e.get('automation_id',''), self.foreground.get('process',''))}
            self.focused_ui, self.selected_ui = metadata(focused), metadata(selected)
            if self.focused_ui:
                self._event('UI_CHANGED', self.foreground, {'focused': self.focused_ui}, persist=False)
        except Exception:
            self.uia_errors += 1
            self.focused_ui = self.selected_ui = None
        self.last_uia_at = monotonic()

    def _screen_sample(self, monitors):
        # Downscaled grayscale only in RAM, one grab per monitor every few seconds.
        if self.paused: return
        for monitor in monitors:
            key = monitor['monitor_id']
            if key in self.excluded_monitors or None in self.excluded_monitors or any(w.get('monitor_id') == key and w.get('title') == '[protected]' and not w.get('minimized') for w in self.windows.values()):
                self.frames.pop(key, None)
                continue
            try:
                box = (monitor['left'], monitor['top'], monitor['right'], monitor['bottom'])
                from kk.ui.capture_guard import ambient_capture
                with ambient_capture() as permitted:
                    if not permitted:
                        self.screen_deferred += 1
                        self.screen_deferred_reason = 'overlay_exclusion_unverified_metadata_only'
                        # No old frame may be called fresh, compared against a
                        # later session, or reused as a current diff region.
                        old = self.frames.pop(key, None)
                        if old is not None:
                            old.close()
                        self.diff_region, self.diff_observed_at = None, 0.0
                        continue
                    captured = ImageGrab.grab(bbox=box, all_screens=True)
                self.screen_deferred_reason = None
                try: image = captured.convert('L')
                finally: captured.close()
                image.thumbnail((128, 72))
                self.screen_samples += 1
                self.screen_samples_by_monitor[key] = self.screen_samples_by_monitor.get(key, 0) + 1
                result = screen_difference(self.frames.get(key), image)
                if result['changed']:
                    change = {'monitor_id': key, 'timestamp': time(), **result}
                    self.screen_changes = (self.screen_changes + [change])[-10:]
                    self.diff_region = list(box)
                    self.diff_observed_at = monotonic()
                    self._event('SCREEN_CHANGED', {'monitor_id': key}, result, persist=False)
                self.frames[key] = image
            except Exception:
                self.screen_errors += 1
                self.frames.pop(key, None)

    def recent_diff_region(self, max_age=5.0):
        with self.lock:
            return self.diff_region if self.diff_region and monotonic() - self.diff_observed_at <= max_age else None

    def snapshot(self) -> dict:
        with self.lock:
            events = self.timeline.recent(600)
            recent = [e['window'] for e in events if e['event'] == 'WINDOW_FOCUSED']
            by_monitor = {}
            for window in self.windows.values():
                if not window.get('minimized'):
                    by_monitor.setdefault(window.get('monitor_id', 'unknown'), []).append(window)
            snap = DesktopContextSnapshot(time(), self.foreground.get('process',''), dict(self.foreground),
                self.foreground.get('monitor_id',''), by_monitor, self.focused_ui, self.selected_ui,
                list(dict.fromkeys(w.get('process','') for w in reversed(recent)))[:8],
                list(reversed(recent))[:8], self.last_action, self.user_state, None,
                self.screen_changes[-5:], self.virtual_bounds, self.monitors, self.active_task)
            return snap.as_dict()

    def recent(self, duration=600, app='', title='', monitor_id='', keywords='') -> dict:
        duration = max(1, min(int(duration), 86400))
        with self.lock:
            events_by_key = {}
            for event in self.timeline.persisted(duration, 200) + self.timeline.recent(duration):
                key = (event['timestamp'], event['event'], event['window'].get('hwnd'))
                events_by_key[key] = event
            events = sorted(events_by_key.values(), key=lambda event: event['timestamp'])
            terms = [term.casefold() for term in keywords.split() if term]
            filtered = [e for e in events if (not app or app.casefold() in e['window'].get('process','').casefold())
                and (not title or title.casefold() in e['window'].get('title','').casefold())
                and (not monitor_id or monitor_id == e['window'].get('monitor_id'))
                and (not terms or any(term in (e['window'].get('title','')+' '+e['window'].get('process','')+' '+
                    e.get('details', {}).get('name', '')).casefold() for term in terms))]
            current = self.foreground if not (app or title or monitor_id or keywords) or any(
                e['event']=='WINDOW_FOCUSED' and e['window'].get('hwnd')==self.foreground.get('hwnd') for e in filtered) else None
            summary = compress_context(filtered, current, duration)
            summary['unresolved_task'] = self.active_task
            return {'duration_seconds': duration, 'summary': summary,
                    'events': [{'timestamp': e['timestamp'], 'event': e['event'], 'window': e['window'], 'details': e.get('details',{})} for e in filtered[-12:]],
                    'total_events': len(filtered)}

    def previous(self, before_app='') -> dict | None:
        with self.lock:
            return previous_window(self.timeline.recent(self.timeline.retention_seconds), self.foreground, before_app)

    def find_app(self, query: str) -> dict:
        needle = query.casefold()
        aliases = {'telegram': 'ayugram', 'телеграм': 'ayugram', 'телега': 'ayugram'}
        needle = aliases.get(needle, needle)
        with self.lock:
            matches = [window for window in self.windows.values() if needle in (window.get('process','')+' '+window.get('title','')).casefold()]
            if matches:
                matches.sort(key=lambda w: (w.get('minimized',False), w.get('hwnd') != self.foreground.get('hwnd')))
                return {'found': True, 'current': True, 'window': matches[0]}
            for event in reversed(self.timeline.recent(self.timeline.retention_seconds)):
                w = event['window']
                if needle in (w.get('process','')+' '+w.get('title','')).casefold() and event['event'] != 'WINDOW_CLOSED':
                    return {'found': True, 'current': False, 'window': w}
            return {'found': False}

    def retrieve_for_query(self, query: str) -> dict | None:
        text = query.casefold()
        if not any(term in text for term in ('где ', 'что я', 'что было', 'последн', 'недавн', 'верни', 'окн', 'монитор', 'экран',
                                              'telegram', 'телеграм', 'ayugram', 'browser', 'браузер',
                                              'нажимал', 'нажал', 'выбирал', 'выбрал', 'клик')):
            return None
        app = ''
        if any(term in text for term in ('telegram', 'телеграм', 'ayugram')):
            app = 'AyuGram'
        monitor_id = ''
        for monitor in self.monitors:
            if str(monitor.get('index')) in text and 'монитор' in text:
                monitor_id = monitor['monitor_id']
                break
        duration = (3600 if any(term in text for term in ('до перерыва', 'перед перерывом', 'до паузы'))
                    else 660 if '10 минут' in text else 600)
        result = self.recent(duration, app=app, monitor_id=monitor_id)
        return {'summary': result['summary'], 'total_events': result['total_events']}

    def return_to_previous(self, before_app='') -> dict:
        from kk.tools.windows.native import foreground_window
        from kk.tools.windows.windows import list_visible_windows, focus_window_handle
        target = self.previous(before_app)
        if not target:
            raise LookupError('Предыдущее окно не найдено в локальной истории')
        windows = list_visible_windows()
        current = next((w for w in windows if w['hwnd'] == target['hwnd'] and
                        w.get('process') == target.get('process') and
                        (not target.get('process_id') or w.get('process_id') == target['process_id']) and
                        w.get('title') == target.get('title')), None)
        if current is None:
            matches = [w for w in windows if w.get('process') == target.get('process') and w.get('title') == target.get('title')]
            if len(matches) != 1:
                raise LookupError('Предыдущее окно закрылось или не определяется однозначно')
            current = matches[0]
        observed = focus_window_handle(current['hwnd'])
        if observed.get('hwnd') != current['hwnd']:
            raise OSError('Не удалось подтвердить возврат к окну')
        return {'restored': True, 'window': safe_window(observed)}

    def record_tool(self, name, arguments, result=None):
        if name in {'get_desktop_context','get_recent_context','get_foreground_window','observe_context'}:
            return
        details = {'tool': name}
        with self.lock:
            if self.paused:
                return
            self.last_action = {'timestamp': time(), **details}
            self._tool_quiet_until = monotonic() + 1.0
            self._event('KK_ACTION', self.foreground, details)
            if result is not None:
                self._event('TOOL_EXECUTED', self.foreground, {'tool': name, 'ok': bool(result)}, persist=False)
                self._event('KK_RESULT', self.foreground, {'tool': name, 'ok': bool(result)})

    def pause(self, paused=True):
        with self.lock:
            was_paused = self.paused
            if paused and not was_paused:
                self.timeline.end_focus()
            self.paused = bool(paused)
            self.ui_event_epoch += 1
            with self.ui_event_lock:
                self.ui_event_queue.clear()
            self.frames.clear()
            self.diff_region, self.diff_observed_at = None, 0.0
            if was_paused and not self.paused:
                # A paused interval is unobserved. Rebuild the live snapshot without
                # interpreting changes during that interval as window events.
                self.windows = {}
                self.foreground = {}
                self.monitors = []
                self.focused_ui = self.selected_ui = None
                self.active_task = None
                self.initialized = False
                self.last_input_at = self.last_cursor = None
                self.last_screen_at = self.last_uia_at = 0
            self.wake_event.set()
            return {'paused': self.paused}

    def observation_counters(self):
        """Content-free lifecycle evidence for Pause/Resume checks."""
        with self.lock:
            return {'paused': self.paused, 'samples': self.samples,
                    'screen_samples': self.screen_samples, 'frames': len(self.frames),
                    'episode_writes': self.timeline.writes, 'epoch': self.ui_event_epoch}

    def reset(self, reason='manual'):
        with self.lock:
            self.ui_event_epoch += 1
            with self.ui_event_lock:
                self.ui_event_queue.clear()
            self.windows.clear(); self.foreground = {}; self.frames.clear(); self.diff_region = None
            self.diff_observed_at = 0.0
            self.last_uia_at = 0; self.last_screen_at = 0
            self._event('CONTEXT_RESET', details={'reason': reason}, persist=False)

    def diagnostics(self):
        import os
        import psutil
        with self.lock:
            elapsed = max(.001, monotonic()-self.started_at)
            return {'paused': self.paused, 'monitors': self.monitors, 'snapshot': self.snapshot(),
                'recent_events': self.timeline.recent(120)[-12:], 'persisted_events': self.timeline.persisted(600,12),
                'compressed_context': self.recent(600)['summary'],
                'active_task': self.active_task, 'process_rss_mb': round(psutil.Process(os.getpid()).memory_info().rss/1048576,2),
                'frame_bytes': sum(len(frame.tobytes()) for frame in self.frames.values()),
                'event_rate_per_minute': round(len(self.timeline.recent(min(elapsed,600))) / min(elapsed,600)*60,2) if elapsed >= 10 else None,
                'event_rate_window_seconds': round(elapsed,2),
                'db_writes': self.timeline.writes, 'samples': self.samples,
                'sample_errors': self.sample_errors, 'uia_errors': self.uia_errors,
                'semantic_ui_events': self.ui_events_recorded, 'semantic_ui_dropped': self.ui_events_dropped,
                'semantic_ui': self.semantic_diagnostics(),
                'screen_errors': self.screen_errors,
                'screen_samples': self.screen_samples,
                'screen_deferred': self.screen_deferred,
                'screen_deferred_reason': self.screen_deferred_reason,
                'screen_samples_by_monitor': dict(self.screen_samples_by_monitor)}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        def run():
            try:
                import comtypes
                comtypes.CoInitializeEx(0)  # COINIT_MULTITHREADED; no UI owns this thread.
                initializer = True
                self._semantic_apartment = 'MTA'
            except Exception as exc:
                initializer = False
                self._semantic_apartment = 'initialization_failed'
                self._semantic_failure('com_initialize', exc)
            try:
                focus_events = None
                semantic_events = None
                try:
                    from kk.vision.uia_events import UIAFocusEvents
                    def on_focus(metadata):
                        self.wake_event.set()
                        with self.lock:
                            if (not self.paused and not metadata.get('protected')
                                    and self.foreground.get('title') != '[protected]'
                                    and metadata.get('process_id') == self.foreground.get('process_id')):
                                self.focused_ui = {'control_type': metadata.get('control_type'),
                                    'automation_id': safe_title(metadata.get('automation_id',''), self.foreground.get('process',''))}
                                self._event('UI_CHANGED', self.foreground, {'focused': self.focused_ui}, persist=False)
                                self.wake_event.set()
                    focus_events = UIAFocusEvents(on_focus)
                except Exception:
                    pass
                try:
                    from kk.vision.uia_events import UIASemanticEvents
                    semantic_events = UIASemanticEvents(self.enqueue_ui_event,
                        enabled=lambda: not self.paused and not self.stop_event.is_set())
                    self._semantic_listener = semantic_events
                    self._semantic_stage = 'ready'
                except Exception as exc:
                    self._semantic_failure('create_listener', exc)
                while not self.stop_event.is_set():
                    if not self.paused:
                        try:
                            self.sample(screen=True)
                        except Exception:
                            self.sample_errors += 1
                    if semantic_events:
                        try:
                            with self.lock:
                                target = dict(self.foreground) if (not self.paused and
                                    self.foreground.get('title') != '[protected]' and
                                    not self._excluded(self.foreground)) else None
                                if target:
                                    target['observation_epoch'] = self.ui_event_epoch
                            semantic_events.watch(target)
                            self._semantic_stage = 'running'
                        except Exception as exc:
                            self._semantic_failure('watch', exc)
                    self._drain_ui_events()
                    self.wake_event.wait(self.interval)
                    self.wake_event.clear()
                    if focus_events:
                        try:
                            import comtypes.client
                            comtypes.client.PumpEvents(.01)
                        except Exception:
                            pass
            finally:
                try:
                    for listener in (semantic_events, focus_events):
                        if listener:
                            try:
                                listener.close()
                            except Exception:
                                self.uia_errors += 1
                finally:
                    self._semantic_stage = 'stopped'
                    if initializer:
                        comtypes.CoUninitialize()
        self.thread = Thread(target=run, name='KKAmbient', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set(); self.wake_event.set()
        with self.ui_event_lock:
            self.ui_event_queue.clear()
        if self.thread:
            self.thread.join(timeout=3)
        self.frames.clear()
        if not self.paused:
            self.timeline.end_focus()
        self.timeline.close()
