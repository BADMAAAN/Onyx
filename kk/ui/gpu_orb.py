"""Qt Quick/D3D11 renderer process. No Brain, STT, UIA or network in this process."""
import ctypes
import json
import os
from pathlib import Path
import sys
from threading import Thread
from time import perf_counter as monotonic
from queue import Queue

os.environ.setdefault('QSG_RHI_BACKEND','d3d11')
os.environ.setdefault('QSG_RENDER_LOOP','threaded')
# DWM already composites this transparent tool window. Qt's additional swap wait
# halved animation throughput on the tested dual-monitor system; timer-driven
# scheduling avoids double throttling and keeps rendering near 60 Hz.
os.environ.setdefault('QSG_NO_VSYNC','1')
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, QRect, QAbstractNativeEventFilter
from PySide6.QtGui import QGuiApplication, QRegion
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from kk.config import Config
from .presence import presence
from .orb_model import load_positions, save_positions, select_monitor, is_fullscreen


class MoveEvents(QAbstractNativeEventFilter):
    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge

    def nativeEventFilter(self, event_type, message):
        from ctypes.wintypes import MSG
        msg = MSG.from_address(int(message))
        bridge = self.bridge
        if bridge.native_hwnd and int(msg.hWnd or 0) == bridge.native_hwnd:
            if msg.message == 0x0231: bridge.dragStarted()
            elif msg.message == 0x0232: bridge.dragEnded()
            elif msg.message == 0x0216:
                bridge.move_pending = monotonic()
        return False, 0


class Bridge(QObject):
    changed = Signal()
    incoming = Signal(dict)
    eof = Signal()
    def __init__(self, app, config):
        super().__init__()
        self.app, self.config = app, config
        self.packet = {}; self.value = {}; self.window = None; self.native_hwnd = 0
        self.dragging = False; self.move_pending = None; self.move_frames = []
        self.move_events = MoveEvents(self); app.installNativeEventFilter(self.move_events)
        self.out = Queue(); self.frames = []; self.last_frame = None; self.started=monotonic()
        self.writes = Queue(maxsize=1)
        self.path = config.root/'data'/'orb_positions_qt.json'
        self.testing = '--self-test' in sys.argv
        self.positions = {} if self.testing else load_positions(self.path)
        self.incoming.connect(self.receive); self.eof.connect(app.quit)
        self.save_timer=QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.persist)
        self.poll=QTimer(self); self.poll.setInterval(200); self.poll.timeout.connect(self.refresh)
        self.poll.start()
        self.report=QTimer(self); self.report.setInterval(2000); self.report.timeout.connect(self.report_metrics); self.report.start()
        Thread(target=self.write,daemon=True).start()
        Thread(target=self.save_worker,daemon=True).start()
        if not self.testing: Thread(target=self.read,daemon=True).start()
    @Property(int, constant=True)
    def size(self): return max(80,min(320,self.config.get('ui.orb_size',144)))
    @Property('QVariantMap', notify=changed)
    def data(self): return self.value
    def write(self):
        while True:
            packet=self.out.get()
            try: sys.stdout.write(json.dumps(packet)+'\n'); sys.stdout.flush()
            except (OSError,ValueError): return
    def read(self):
        for line in sys.stdin:
            try: self.incoming.emit(json.loads(line))
            except ValueError: continue
        self.eof.emit()
    @Slot(dict)
    def receive(self, packet):
        self.packet=packet
        self.refresh()
    @Slot(str)
    def action(self, name): self.out.put({'action':name,'at':monotonic()})
    @Slot()
    def dragStarted(self):
        self.drag_at=monotonic(); self.dragging=True; self.save_timer.stop()
    @Slot()
    def dragEnded(self):
        self.dragging=False
        self.save_timer.start()
    @Slot()
    def savePosition(self):
        if not self.dragging: self.save_timer.start()
    def persist(self):
        if self.testing: return
        if not self.window: return
        screen=self.window.screen(); pos=[self.window.x(),self.window.y()]
        self.positions[screen.name()]=pos; self.positions['_last_monitor']=screen.name()
        # The GUI only snapshots. Disk IO is off the input/render thread.
        positions=dict(self.positions)
        from queue import Full, Empty
        try: self.writes.put_nowait(positions)
        except Full:
            try:
                self.writes.get_nowait()
                self.writes.task_done()
            except Empty: pass
            self.writes.put_nowait(positions)
        self.out.put({'position':pos,'monitor':screen.name()})
    def save_worker(self):
        while True:
            positions=self.writes.get()
            try: save_positions(self.path,positions)
            except OSError as exc: print('Cannot persist Orb position: '+str(exc),file=sys.stderr)
            finally: self.writes.task_done()
    def frame(self):
        now=monotonic()
        if self.move_pending is not None:
            self.move_frames.append((now-self.move_pending)*1000)
            self.move_frames=self.move_frames[-600:]
            self.move_pending=None
        if self.last_frame and now-self.last_frame<.5: self.frames.append(now-self.last_frame)
        self.last_frame=now
        self.frames=self.frames[-600:]
    def report_metrics(self):
        if not self.window: return
        import statistics
        metrics={'backend':str(self.window.rendererInterface().graphicsApi()),'dpr':self.window.devicePixelRatio(),
            'frame_count':len(self.frames),'frame_ms_median':statistics.median(self.frames)*1000 if self.frames else None,
            'frame_ms_p95':sorted(self.frames)[int((len(self.frames)-1)*.95)]*1000 if self.frames else None}
        metrics.update(move_to_frame_ms_p95=sorted(self.move_frames)[int((len(self.move_frames)-1)*.95)] if self.move_frames else None,
                       move_samples=len(self.move_frames), movement_basis='WM_MOVING to frameSwapped; not acoustic/physical pointer-to-photon')
        self.out.put({'metrics':metrics,'hwnd':int(self.window.winId()),'position':[self.window.x(),self.window.y()], 'monitor':self.window.screen().name()})
    def refresh(self):
        from .orb_window import foreground_info
        from kk.tools.windows.native import list_monitors
        now=monotonic()
        if not hasattr(self,'monitors') or now-self.topology_at>2:
            self.monitors=list_monitors(); self.topology_at=now
        if not hasattr(self,'fg_at') or now-self.fg_at>.2:
            fg=foreground_info(); monitor=select_monitor(self.monitors,cursor=fg.get('center'))
            self.fullscreen=is_fullscreen(fg,monitor); self.fg_at=now
            self.active_monitor=monitor
        screens=self.app.screens()
        # Qt exposes friendly screen names; Win32 exposes DISPLAYn identifiers.
        # Windows per-monitor logical origins match native display origins.
        screen=min(screens,key=lambda s: abs(s.geometry().x()-self.active_monitor['left'])+abs(s.geometry().y()-self.active_monitor['top'])) if self.active_monitor else self.app.primaryScreen()
        rect=screen.geometry()
        flags=presence(self.packet.get('visible',True),self.packet.get('active',False),self.fullscreen,
            self.config.get('orb.hide_in_fullscreen',True),self.config.get('orb.fullscreen_voice_edge',True))
        status=self.packet.get('state','IDLE')
        activity={'LISTENING':.45,'USER_SPEAKING':.55,'RECORDING':.7,'COMMITTING':.85,'THINKING':.8,'ACTING':1,'SPEAKING':.65}.get(status,.05)
        edge_screens=[dict(x=s.geometry().x(),y=s.geometry().y(),width=s.geometry().width(),
                          height=s.geometry().height(),active=s==screen,name=s.name()) for s in screens]
        value=dict(self.packet,**flags,activity=activity,edgeX=rect.x(),edgeY=rect.y(),edgeWidth=rect.width(),edgeHeight=rect.height(),edgeScreens=edge_screens)
        if value != self.value:
            self.value = value
            self.changed.emit()


def main():
    app=QGuiApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    config=Config.load(Path(sys.argv[1])/'config.toml')
    bridge=Bridge(app,config)
    engine=QQmlApplicationEngine(); engine.rootContext().setContextProperty('bridge',bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('Orb.qml'))))
    if not engine.rootObjects(): return 2
    window=engine.rootObjects()[0]; bridge.window=window
    bridge.native_hwnd=int(window.winId())
    # Logical coordinates remain local to Qt; its swapchain uses physical DPR pixels.
    screens=app.screens(); screen=next((s for s in screens if s.name()==bridge.positions.get('_last_monitor')),app.primaryScreen())
    rect=screen.availableGeometry(); saved=bridge.positions.get(screen.name(),[rect.right()-bridge.size-24,rect.bottom()-bridge.size-24])
    window.setPosition(max(rect.left(),min(saved[0],rect.right()-bridge.size)),max(rect.top(),min(saved[1],rect.bottom()-bridge.size)))
    window.setMask(QRegion(QRect(3,3,bridge.size-6,bridge.size-6),QRegion.Ellipse))
    window.xChanged.connect(bridge.savePosition); window.yChanged.connect(bridge.savePosition)
    window.frameSwapped.connect(bridge.frame)
    bridge.refresh(); bridge.report_metrics()
    if bridge.testing:
        from .gpu_check import exercise
        exercise(app, bridge, window)
    app.aboutToQuit.connect(bridge.persist)
    code=app.exec()
    bridge.writes.join()  # shutdown only; never blocks dragging or rendering
    return code


if __name__=='__main__': sys.exit(main())
