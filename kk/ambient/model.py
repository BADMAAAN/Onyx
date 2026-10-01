"""Privacy bounded, deterministic activity history. No screen content is stored."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import re
import sqlite3
from threading import RLock
from time import time
from typing import Any


SENSITIVE = re.compile(r'(?i)password|passcode|one.?time|\botp\b|authenticat|secret|private.?key|cookie|token|bitwarden|keepass|1password|lastpass|диспетчер парол|парол|код подтвержден|inprivate|private browsing|приватн|инкогнито|[?&](code|access_token|refresh_token|api_key)=')


def safe_title(title: str, process: str = '') -> str:
    title = str(title or '')[:180].replace('\n', ' ').replace('\r', ' ')
    return '[protected]' if SENSITIVE.search(title + ' ' + process) else title


def safe_window(window: dict) -> dict:
    return {key: value for key, value in {
        'hwnd': int(window.get('hwnd') or 0),
        'process_id': int(window.get('process_id') or 0),
        'process': str(window.get('process') or '')[:80],
        'title': safe_title(window.get('title', ''), window.get('process', '')),
        'monitor_id': window.get('monitor_id'),
        'monitor_index': window.get('monitor_index'),
        'bounds': window.get('bounds'),
        'minimized': bool(window.get('minimized', False)),
    }.items() if value is not None}


@dataclass(slots=True)
class DesktopContextSnapshot:
    timestamp: float
    foreground_app: str = ''
    foreground_window: dict = field(default_factory=dict)
    foreground_monitor: str = ''
    windows_by_monitor: dict = field(default_factory=dict)
    focused_ui_element: dict | None = None
    selected_ui_element: dict | None = None
    recent_apps: list = field(default_factory=list)
    recent_windows: list = field(default_factory=list)
    last_kk_action: dict | None = None
    recent_user_activity_state: str = 'unknown'
    relevant_ocr_text: str | None = None
    significant_screen_changes: list = field(default_factory=list)
    virtual_desktop_bounds: list = field(default_factory=list)
    monitors: list = field(default_factory=list)
    active_task: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ActiveUserTask:
    application: str
    document: str
    started: float
    last_activity: float
    project: str | None = None
    files: list[str] = field(default_factory=list)
    source: str = 'foreground_window'


class ContextTimeline:
    def __init__(self, path, retention_seconds=900, max_events=800, coalesce_seconds=3):
        self.path = path
        self.retention_seconds = retention_seconds
        self.events = deque(maxlen=max_events)
        self.lock = RLock()
        self.coalesce_seconds = coalesce_seconds
        self.session_id = 0
        self.db = sqlite3.connect(path, timeout=.2, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, started REAL NOT NULL, ended REAL NOT NULL, session INTEGER NOT NULL, kind TEXT NOT NULL, process TEXT, title TEXT, monitor_id TEXT, hwnd INTEGER, bounds TEXT, count INTEGER NOT NULL DEFAULT 1, details TEXT)')
        if 'details' not in {column[1] for column in self.db.execute('PRAGMA table_info(activity)')}:
            self.db.execute('ALTER TABLE activity ADD COLUMN details TEXT')
        self.db.execute('CREATE INDEX IF NOT EXISTS activity_time ON activity(started)')
        self.db.commit()
        self.writes = 0

    def add(self, kind: str, window: dict | None = None, *, at: float | None = None, details: dict | None = None, persist=True) -> dict:
        import json
        at = time() if at is None else at
        window = safe_window(window or {})
        details = details or {}
        event = {'timestamp': at, 'event': kind, 'window': window, 'details': details, 'session': self.session_id, 'count': 1}
        signature = (kind, window.get('hwnd'), window.get('title'), window.get('monitor_id'), str(details))
        with self.lock:
            self.prune(at)
            if kind in {'UI_CHANGED','USER_INPUT','SCREEN_CHANGED'}:
                for previous in reversed(list(self.events)[-20:]):
                    previous_signature = (previous['event'], previous['window'].get('hwnd'), previous['window'].get('title'), previous['window'].get('monitor_id'), str(previous['details']))
                    if signature == previous_signature and at - previous.get('ended', previous['timestamp']) <= self.coalesce_seconds:
                        previous['count'] += 1
                        previous['ended'] = at
                        return previous
            if self.events:
                last = self.events[-1]
                last_signature = (last['event'], last['window'].get('hwnd'), last['window'].get('title'), last['window'].get('monitor_id'), str(last['details']))
                if signature == last_signature and at - last['timestamp'] <= self.coalesce_seconds:
                    last['count'] += 1
                    last['ended'] = at
                    return last
            self.events.append(event)
            if persist and kind in {'WINDOW_FOCUSED','WINDOW_OPENED','WINDOW_CLOSED','WINDOW_MOVED','WINDOW_MINIMIZED','WINDOW_RESTORED','MONITOR_CHANGED','WINDOW_TITLE_CHANGED','APP_STARTED','APP_EXITED','USER_IDLE','USER_ACTIVE','KK_ACTION','KK_RESULT','SESSION_STARTED','USER_UI_INVOKED','USER_UI_SELECTED'}:
                try:
                    cursor = self.db.execute('INSERT INTO activity(started,ended,session,kind,process,title,monitor_id,hwnd,bounds,count,details) VALUES (?,?,?,?,?,?,?,?,?,1,?)',
                        (at, at, self.session_id, kind, window.get('process'), window.get('title'), window.get('monitor_id'), window.get('hwnd'), json.dumps(window.get('bounds')), json.dumps(details, ensure_ascii=False)))
                    self.db.commit()
                    event['source_ref'] = f'ambient:activity:{cursor.lastrowid}'
                    self.writes += 1
                except sqlite3.OperationalError:
                    self.db.rollback()
            return event

    def prune(self, now=None):
        cutoff = (time() if now is None else now) - self.retention_seconds
        while self.events and self.events[0]['timestamp'] < cutoff:
            self.events.popleft()

    def end_focus(self, at=None):
        at = time() if at is None else at
        with self.lock:
            for event in reversed(self.events):
                if event['event'] == 'WINDOW_FOCUSED' and 'ended' not in event:
                    event['ended'] = max(at, event['timestamp'])
                    break
            try:
                row = self.db.execute("SELECT id,started FROM activity WHERE kind='WINDOW_FOCUSED' AND ended=started ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    self.db.execute('UPDATE activity SET ended=? WHERE id=?', (max(at,row[1]), row[0]))
                    self.db.commit()
                    self.writes += 1
            except sqlite3.OperationalError:
                self.db.rollback()

    def recent(self, duration=600, limit=100, *, now=None) -> list[dict]:
        now = time() if now is None else now
        with self.lock:
            self.prune(now)
            return [dict(event) for event in self.events if event['timestamp'] >= now - max(1, duration)][-limit:]

    def persisted(self, duration=86400, limit=100, *, now=None) -> list[dict]:
        import json
        now = time() if now is None else now
        with self.lock:
            try:
                rows = self.db.execute('SELECT started,ended,session,kind,process,title,monitor_id,hwnd,bounds,count,details,id FROM activity WHERE started>=? ORDER BY id DESC LIMIT ?', (now-duration, limit)).fetchall()
            except sqlite3.OperationalError:
                return []
        return [{'timestamp': r[0], 'ended': r[1], 'session': r[2], 'event': r[3], 'source_ref': f'ambient:activity:{r[11]}',
                 'window': {'process': r[4], 'title': r[5], 'monitor_id': r[6], 'hwnd': r[7], 'bounds': json.loads(r[8]) if r[8] else None},
                 'count': r[9], 'details': json.loads(r[10]) if r[10] else {}} for r in reversed(rows)]

    def close(self):
        with self.lock:
            self.db.close()


def previous_window(events: list[dict], current: dict, before_app: str = '') -> dict | None:
    """Find last distinct foreground window before the named app/current window."""
    focus = [e['window'] for e in events if e['event'] == 'WINDOW_FOCUSED' and e['window'].get('hwnd')]
    if not focus:
        return None
    needle = before_app.casefold()
    index = len(focus) - 1
    if needle:
        index = next((i for i in range(len(focus)-1, -1, -1) if needle in (focus[i].get('process','')+' '+focus[i].get('title','')).casefold()), -1)
        if index < 0:
            return None
    else:
        while index >= 0 and focus[index].get('hwnd') == current.get('hwnd'):
            index -= 1
    candidates = focus[:index] if needle else focus[:index+1]
    for candidate in reversed(candidates):
        if candidate.get('hwnd') != current.get('hwnd') and (not needle or needle not in (candidate.get('process','')+' '+candidate.get('title','')).casefold()):
            return candidate
    return None


def compress_context(events: list[dict], current: dict | None = None, duration=600, max_transitions=6) -> dict:
    """Bounded structure using observed windows and durations only."""
    if not events:
        return {'current_activity': current or None, 'previous_activity': None, 'activities': [], 'focus_sequence': [], 'important_transitions': [], 'kk_actions': [], 'user_ui_actions': [], 'unresolved_task': None}
    focus = [e for e in events if e['event'] == 'WINDOW_FOCUSED']
    activities = []
    end = events[-1]['timestamp']
    for i, event in enumerate(focus):
        nxt = focus[i+1]['timestamp'] if i+1 < len(focus) else end
        if event.get('ended', event['timestamp']) > event['timestamp']:
            nxt = min(nxt, event['ended'])
        idle = next((e['timestamp'] for e in events if e['event']=='USER_IDLE' and event['timestamp'] < e['timestamp'] < nxt), None)
        if idle is not None:
            nxt = idle
        window = event['window']
        key = (window.get('process'), window.get('title'), window.get('monitor_id'))
        seconds = max(0, min(nxt-event['timestamp'], duration))
        item = next((a for a in activities if (a['process'], a['title'], a['monitor_id']) == key), None)
        if item is None:
            item = {'process': key[0], 'title': key[1], 'monitor_id': key[2], 'seconds': 0}
            activities.append(item)
        item['seconds'] += seconds
    activities.sort(key=lambda a: a['seconds'], reverse=True)
    transitions = [{'event': e['event'], 'window': e['window'], 'details': e.get('details',{})} for e in events if e['event'] in {'WINDOW_OPENED','WINDOW_CLOSED','WINDOW_MINIMIZED','WINDOW_RESTORED','MONITOR_CHANGED','WINDOW_TITLE_CHANGED','APP_STARTED','APP_EXITED','USER_IDLE','USER_ACTIVE','SESSION_STARTED'}][-max_transitions:]
    focus_sequence = []
    for event in events:
        if event['event'] not in {'WINDOW_FOCUSED','SESSION_STARTED','USER_IDLE'}:
            continue
        item = {'event': event['event'], 'timestamp': event['timestamp']}
        if event['event'] == 'WINDOW_FOCUSED':
            window = event['window']
            item.update(process=window.get('process'), title=window.get('title'), monitor_id=window.get('monitor_id'))
        if focus_sequence and item.get('event') == 'WINDOW_FOCUSED' and all(
            focus_sequence[-1].get(key) == item.get(key) for key in ('event','process','title','monitor_id')):
            continue
        focus_sequence.append(item)
    actions = [{'event': e['event'], 'details': e.get('details',{})} for e in events if e['event'] in {'KK_ACTION','KK_RESULT'}][-4:]
    user_ui = [{'timestamp': e['timestamp'], 'event': e['event'], 'window': e['window'], 'source_ref': e.get('source_ref'),
                'details': e.get('details', {})} for e in events if e['event'] in {'USER_UI_INVOKED','USER_UI_SELECTED'}][-8:]
    return {'current_activity': current or (focus[-1]['window'] if focus else None),
        'previous_activity': previous_window(events, current or (focus[-1]['window'] if focus else {})),
        'activities': activities[:8], 'focus_sequence': focus_sequence[-12:],
        'important_transitions': transitions, 'kk_actions': actions,
        'user_ui_actions': user_ui,
        'unresolved_task': None}
