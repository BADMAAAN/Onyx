"""Qt Quick / D3D11 output-only per-monitor Edge renderer subprocess."""
import ctypes
import hashlib
from ctypes import wintypes
import json
import os
from pathlib import Path
from collections import Counter, deque
from queue import Queue
import statistics
import sys
from threading import Thread
from time import perf_counter as monotonic
from datetime import datetime, timezone

os.environ.setdefault('QSG_RHI_BACKEND', 'd3d11')
os.environ.setdefault('QSG_RENDER_LOOP', 'threaded')
# Retain the measured DWM scheduling setting of the existing Qt renderer.
os.environ.setdefault('QSG_NO_VSYNC', '1')

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
import shiboken6
from kk.config import Config
from .edge_model import EdgeSession, edge_presence
from .orb_model import is_fullscreen, select_monitor


class EdgeBridge(QObject):
    changed = Signal()
    screensChanged = Signal()
    incoming = Signal(dict)
    eof = Signal()

    def __init__(self, app, config):
        super().__init__()
        self.app, self.config = app, config
        self.packet, self.value, self.screen_rows = {}, {}, []
        self.windows = {}
        self.frames, self.last_frames = {}, {}
        self.frame_totals = Counter()
        self.lifecycle_counts = Counter()
        self.lifecycle_events = deque(maxlen=64)
        self.session = EdgeSession()
        self.capture_hidden = False
        self.foreground_at, self.foreground = -1., ('', False)
        self.out = Queue()
        self.incoming.connect(self.receive)
        self.eof.connect(app.quit)
        self.poll = QTimer(self)
        self.poll.setInterval(200)
        self.poll.timeout.connect(self.refresh)
        self.report = QTimer(self)
        self.report.setInterval(2000)
        self.report.timeout.connect(self.report_metrics)
        app.screenAdded.connect(self.connect_screen)
        app.screenRemoved.connect(self.topology)
        for screen in app.screens():
            self.connect_screen(screen)
        Thread(target=self.write, daemon=True, name='EdgeIPCWrite').start()
        import PySide6
        source = hashlib.sha256()
        for name in ('gpu_edge.py', 'edge_model.py', 'Edge.qml', 'edge.frag.qsb'):
            source.update(name.encode())
            source.update(Path(__file__).with_name(name).read_bytes())
        self.out.put({'renderer_identity': {
            'started_at': datetime.now(timezone.utc).isoformat(),
            'pid': os.getpid(), 'executable': sys.executable, 'module': 'kk.ui.gpu_edge',
            'source_sha256': source.hexdigest(), 'qt_version': PySide6.__version__,
            'requested_backend': os.environ.get('QSG_RHI_BACKEND'),
            'config_path': str(config.root/'config.toml'),
            'config_sha256': hashlib.sha256((config.root/'config.toml').read_bytes()).hexdigest()}})

    def start_input(self):
        Thread(target=self.read, daemon=True, name='EdgeIPCRead').start()

    @Property('QVariantMap', notify=changed)
    def data(self):
        return self.value

    @Property('QVariantList', notify=screensChanged)
    def screens(self):
        return self.screen_rows

    def read(self):
        for line in sys.stdin:
            try:
                self.incoming.emit(json.loads(line))
            except ValueError:
                continue
        self.eof.emit()

    def write(self):
        while True:
            try:
                packet = self.out.get()
                # Windows venv pythonw can be a launcher parent. HWND ownership
                # belongs to this process, not necessarily subprocess.Popen.pid.
                packet['renderer_pid'] = os.getpid()
                sys.stdout.write(json.dumps(packet)+'\n')
                sys.stdout.flush()
            except (OSError, ValueError):
                return

    def connect_screen(self, screen):
        screen.geometryChanged.connect(self.topology)
        screen.logicalDotsPerInchChanged.connect(self.topology)
        self.topology()

    def topology(self, *_args):
        # Qt screen origins and logical sizes are used as returned; multiplying
        # the global origin by DPR breaks Windows' mixed-DPI screen islands.
        rows = [dict(name=s.name(), x=s.geometry().x(), y=s.geometry().y(),
                     width=s.geometry().width(), height=s.geometry().height(),
                     dpr=s.devicePixelRatio()) for s in self.app.screens()]
        if rows != self.screen_rows:
            self.screen_rows = rows
            self.foreground_at = -1.
            self.screensChanged.emit()
        self.refresh()

    @Slot(QObject, str)
    def registerWindow(self, window, name):
        # Slot(QObject) can expose QML Window through a QWindow proxy. This
        # delegate is a known Qt Quick Window; register its precise wrapper so
        # rendererInterface/frameSwapped use the actual per-window renderer.
        if not isinstance(window, QQuickWindow):
            window = shiboken6.wrapInstance(shiboken6.getCppPointer(window)[0], QQuickWindow)
        screen = next((s for s in self.app.screens() if s.name() == name), None)
        if screen is not None:
            window.setScreen(screen)
        hwnd = int(window.winId())
        self.windows[hwnd] = window
        self.lifecycle_event('window_registered', hwnd=hwnd)
        self.frames[hwnd] = []
        window.frameSwapped.connect(lambda: self.frame(hwnd))
        window.visibleChanged.connect(self.update_timers)
        window.visibleChanged.connect(lambda: self.visibility_changed(hwnd))
        window.destroyed.connect(lambda: self.unregister_window(hwnd))
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        affinity = bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), 0x11))
        self.out.put({'hwnds': list(self.windows), 'capabilities': {
            'monitor': name, 'hwnd': hwnd, 'wda_excludefromcapture': affinity,
            'wda_error': 0 if affinity else ctypes.get_last_error(),
            'capture_policy': 'explicit_hide_then_dwmflush_ambient_metadata',
            'exclusion_pixel_verified': False,
            'exclusive_fullscreen': 'best_effort_no_injection'}})

    def lifecycle_event(self, event, **details):
        self.lifecycle_counts[event] += 1
        self.lifecycle_events.append({'monotonic': monotonic(), 'event': event, **details})

    def visibility_changed(self, hwnd):
        window = self.windows.get(hwnd)
        if window:
            self.lifecycle_event('shown' if window.isVisible() else 'hidden', hwnd=hwnd,
                                 capture=self.capture_hidden)
            self.report_metrics()

    def unregister_window(self, hwnd):
        self.windows.pop(hwnd, None)
        self.lifecycle_event('window_destroyed', hwnd=hwnd)
        self.frames.pop(hwnd, None)
        self.last_frames.pop(hwnd, None)
        self.out.put({'hwnds': list(self.windows)})

    @Slot(dict)
    def receive(self, packet):
        if packet.get('control') == 'diagnostics':
            self.report_metrics()
            return
        if packet.get('control') == 'probe_affinity':
            # Only the isolated own-surface diagnostic can remove capture
            # exclusion. Production startup never enables this control.
            ok = False
            if '--probe' in sys.argv:
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
                user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
                affinity = 0x11 if packet.get('enabled', True) else 0
                ok = all(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), affinity)
                         for hwnd in self.windows)
                ctypes.WinDLL('dwmapi').DwmFlush()
            self.out.put({'capture_ack': packet.get('token'), 'ok': bool(ok)})
            return
        if packet.get('control') == 'capture':
            self.capture_hidden = bool(packet.get('hidden'))
            self.lifecycle_event('capture_hide' if self.capture_hidden else 'capture_restore')
            self.refresh()
            # QML bindings are synchronous; one event-loop turn also lets Qt
            # submit the hide before the compositor completion handshake.
            QTimer.singleShot(0, lambda: self.capture_ack(packet))
            return
        self.packet = packet
        self.refresh()

    def capture_ack(self, packet):
        try:
            dwm = ctypes.WinDLL('dwmapi')
            dwm.DwmFlush.restype = ctypes.c_long
            ok = dwm.DwmFlush() == 0
            if packet.get('hidden'):
                ok = ok and all(not window.isVisible() for window in self.windows.values())
            self.out.put({'capture_ack': packet['token'], 'ok': ok})
        except (OSError, AttributeError):
            self.out.put({'capture_ack': packet['token'], 'ok': False})

    def current_screen(self):
        from .orb_window import foreground_info
        from kk.tools.windows.native import list_monitors
        fg = foreground_info()
        monitors = list_monitors()
        monitor = select_monitor(monitors, cursor=fg.get('center'))
        # Physical origins on Windows are unchanged by Qt scaling; only local
        # screen sizes are logical. No physical foreground bounds reach QML.
        screen = min(self.screen_rows, key=lambda s: abs(s['x']-monitor['left'])+
                     abs(s['y']-monitor['top'])) if monitor and self.screen_rows else {}
        return screen.get('name', ''), is_fullscreen(fg, monitor) if monitor else False

    def refresh(self):
        now = monotonic()
        if (now-self.foreground_at >= .2 or
                self.packet.get('activation_serial', 0) > self.session.activation_serial):
            self.foreground = self.current_screen() if self.screen_rows else ('', False)
            self.foreground_at = now
        screen, fullscreen = self.foreground
        active = self.session.update(self.packet, now, screen)
        if self.session.active_screen not in {s['name'] for s in self.screen_rows}:
            self.session.active_screen = screen
        get = self.config.get
        hidden = bool(self.packet.get('user_visual_hidden', False))
        fullscreen_hidden = fullscreen and get('orb.hide_in_fullscreen', True)
        flags = edge_presence(user_visual_hidden=hidden, fullscreen_hidden=fullscreen_hidden,
            readiness=self.packet.get('readiness', False), active=active,
            explicitly_activated=self.session.explicit,
            fullscreen_voice_edge=get('orb.fullscreen_voice_edge', True))
        status = self.packet.get('state', 'IDLE')
        if status == 'ERROR' and now >= self.session.error_until:
            status = 'LISTENING' if active else 'IDLE'
        simplified = bool(self.packet.get('simplified'))
        value = {**self.packet, **flags}
        value.update(captureHidden=self.capture_hidden, activeScreen=self.session.active_screen,
            waveSerial=self.session.wave_serial, visualState=status,
            intensity=max(0., min(1., float(get('ui.edge_intensity', .75)))) * (.35 if fullscreen_hidden else 1.),
            reducedMotion=bool(get('ui.edge_reduced_motion', True) or simplified),
            calmMode=bool(get('ui.edge_calm_mode', True) or simplified),
            halo=max(16., min(96., float(get('ui.edge_halo', 64)))),
            appearMs=max(1, int(get('ui.edge_appear_ms', 420))),
            fadeMs=max(1, int(get('ui.edge_fade_ms', 650))))
        if value != self.value:
            self.value = value
            self.changed.emit()
        self.update_timers()

    def update_timers(self, *_args):
        # Foreground checks continue only during an allowed active session.
        polling = (self.session.active and not self.value.get('user_visual_hidden')
                   and not self.capture_hidden)
        if polling and not self.poll.isActive(): self.poll.start()
        elif not polling: self.poll.stop()
        visible = any(w.isVisible() for w in self.windows.values())
        if visible and not self.report.isActive(): self.report.start()
        elif not visible:
            self.report.stop()
            self.last_frames.clear()

    def frame(self, hwnd):
        self.frame_totals[hwnd] += 1
        now = monotonic()
        last = self.last_frames.get(hwnd)
        if last and now-last < .5:
            samples = self.frames.setdefault(hwnd, [])
            samples.append((now-last)*1000)
            del samples[:-600]
        self.last_frames[hwnd] = now

    def report_metrics(self):
        rows = []
        for hwnd, window in list(self.windows.items()):
            samples = self.frames.get(hwnd, [])
            rows.append(dict(hwnd=hwnd, monitor=window.screen().name(),
                dpr=window.devicePixelRatio(), visible=window.isVisible(),
                backend=str(window.rendererInterface().graphicsApi()), samples=len(samples),
                frame_count=self.frame_totals[hwnd],
                frame_ms_median=statistics.median(samples) if samples else None,
                frame_ms_p95=sorted(samples)[int((len(samples)-1)*.95)] if samples else None))
        self.out.put({'hwnds': list(self.windows), 'metrics': rows,
                     'metrics_monotonic': monotonic(),
                     'lifecycle': {'counters': dict(self.lifecycle_counts),
                                   'timeline': list(self.lifecycle_events),
                                   'calm_mode': self.value.get('calmMode', True),
                                   'poll_timer_running': self.poll.isActive(),
                                   'report_timer_running': self.report.isActive()}})


def main():
    app = QGuiApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    config = Config.load(Path(sys.argv[1])/'config.toml')
    bridge = EdgeBridge(app, config)
    engine = QQmlApplicationEngine()
    engine.setInitialProperties({'bridge': bridge})
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('Edge.qml'))))
    if not engine.rootObjects(): return 2
    bridge.start_input()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
