"""Opt-in interaction timings; never record chat, screen, audio or file content."""
from collections import deque
import json
from pathlib import Path
from threading import Event, Lock, Thread
from time import perf_counter
from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtGui import QGuiApplication


class PerformanceProbe(QObject):
    def __init__(self, window, destination, client=None):
        super().__init__(window)
        self.window, self.client = window, client
        self.path = Path(destination)
        self.started = perf_counter()
        self.frames, self.ticks = deque(maxlen=30000), deque(maxlen=30000)
        self.events, self.contexts = deque(maxlen=8000), deque(maxlen=4000)
        self.stop, self.lock = Event(), Lock()
        self.last_tick = self.last_frame = self.started
        self.last_context, self.next_context = None, 0
        self.picker = window.findChild(QObject, 'modelPicker')
        window.frameSwapped.connect(self.frame, Qt.DirectConnection)
        window.installEventFilter(self)
        if client is not None:
            client.contentChanged.connect(lambda: self.record_event('MessageContentChanged'))
            client.sidebarChanged.connect(lambda: self.record_event('SidebarChanged'))
            client.selectionChanged.connect(lambda: self.record_event('ConversationSelected'))
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.capture_context()
        self.timer.start(16)
        self.writer = Thread(target=self.write_loop, name='OnyxPerformanceIO', daemon=True)
        self.writer.start()

    def frame(self):
        now = perf_counter()
        with self.lock:
            self.frames.append((round(now-self.started, 4), round((now-self.last_frame)*1000, 3)))
            self.last_frame = now

    def tick(self):
        now = perf_counter()
        with self.lock:
            self.ticks.append((round(now-self.started, 4), round((now-self.last_tick)*1000, 3)))
            self.last_tick = now
        if now >= self.next_context:
            self.capture_context()
            self.next_context = now + .1

    def record_event(self, name):
        with self.lock:
            self.events.append((round(perf_counter()-self.started, 4), name))

    def capture_context(self):
        window, screen = self.window, self.window.screen()
        context = dict(visible=window.isVisible(), exposed=window.isExposed(),
                       visibility=window.visibility().name, section=window.property('section'),
                       response_active=bool(self.client and self.client.busy),
                       model_picker=bool(self.picker and self.picker.property('visible')),
                       screen=screen.name() if screen else None,
                       dpr=window.devicePixelRatio(),
                       geometry=window.geometry().getRect(),
                       screens=[dict(name=s.name(), area=s.availableGeometry().getRect(),
                                     dpr=s.devicePixelRatio()) for s in QGuiApplication.screens()])
        if context != self.last_context:
            with self.lock:
                self.contexts.append((round(perf_counter()-self.started, 4), context))
            self.last_context = context

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Move, QEvent.Resize, QEvent.WindowStateChange,
                            QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                            QEvent.Expose, QEvent.Show, QEvent.Hide):
            self.record_event(event.type().name)
        return False

    def write_loop(self):
        self.path.mkdir(parents=True, exist_ok=True)
        while True:
            finished = self.stop.wait(3)
            with self.lock:
                snapshot = dict(schema=2, frames=list(self.frames), gui_intervals=list(self.ticks),
                                events=list(self.events), contexts=list(self.contexts))
            snapshot.update(units='seconds since launch, milliseconds interval',
                            note='Frame gaps include idle/occluded time. Native Windows modal moves can pause GUI timers; correlate both traces with geometry events and screenshots.')
            target = self.path/'performance.json'
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(snapshot), encoding='utf-8')
            temporary.replace(target)
            if finished:
                return

    def close(self):
        self.timer.stop()
        self.capture_context()
        self.stop.set()
        self.writer.join(2)
