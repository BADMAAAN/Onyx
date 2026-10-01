"""Real click/scroll delivery through Edge above ordinary (non-topmost) fixtures.

No screenshots, browser observation, network, or backend instance. Coordinator
must stop the backend first. Every injection verifies an owned native target;
actual z-order and foreground are recorded inside the Qt delivery callback.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import sleep

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'data/repair_speech_targets/aura_input_standard.json'


def main():
    if (ROOT/'data/runtime_control.json').exists():
        raise RuntimeError('Coordinator must stop Onyx first')
    app = QApplication(sys.argv)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetTopWindow.argtypes = [wintypes.HWND]
    user32.GetTopWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    shared = {'hwnds': [], 'packets': []}
    windows, events, finished = [], [], Event()
    report = {'source': 'real Qt delivery, ordinary non-topmost owned fixtures',
              'test_automation': True, 'status': 'UNVERIFIED', 'events': events}

    def z_order():
        ranks = {}
        hwnd, rank = user32.GetTopWindow(None), 0
        own = set(shared['hwnds']) | {int(w.winId()) for w in windows}
        while hwnd:
            if int(hwnd) in own: ranks[int(hwnd)] = rank
            hwnd, rank = user32.GetWindow(hwnd, 2), rank+1
        return ranks

    class Fixture(QWidget):
        def __init__(self, screen):
            super().__init__()
            self.monitor = screen.name()
            self.setWindowTitle('Onyx Standard Input Fixture')
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
            self.setGeometry(screen.geometry())
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#314256'))
        def record(self, kind, event):
            hwnd = int(self.winId())
            ranks = z_order()
            events.append({'kind': kind, 'monitor': self.monitor, 'fixture_hwnd': hwnd,
                'point': [event.position().x(), event.position().y()],
                'foreground_hwnd': int(user32.GetForegroundWindow() or 0), 'z_order': ranks,
                'overlay_above_fixture': all(ranks.get(overlay, 99999) < ranks.get(hwnd, -1)
                                             for overlay in shared['hwnds'])})
            event.accept()
        def mousePressEvent(self, event): self.record('click', event)
        def wheelEvent(self, event): self.record('scroll', event)

    for screen in app.screens():
        fixture = Fixture(screen)
        windows.append(fixture)
        fixture.show()
    def run():
        process = None
        try:
            sleep(.7)
            process = subprocess.Popen([sys.executable, '-m', 'kk.ui.gpu_edge', str(ROOT)], cwd=ROOT,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', bufsize=1, env=dict(os.environ, PYTHONUTF8='1'),
                creationflags=subprocess.CREATE_NO_WINDOW)
            def read():
                for line in process.stdout:
                    packet = json.loads(line); shared['packets'].append(packet)
                    if 'hwnds' in packet: shared['hwnds'] = packet['hwnds']
            Thread(target=read, daemon=True).start()
            for _ in range(100):
                if len(shared['hwnds']) == len(windows): break
                sleep(.05)
            if len(shared['hwnds']) != len(windows): raise RuntimeError('Incomplete renderer topology')
            process.stdin.write(json.dumps(dict(state='LISTENING', active=True, activation_serial=1))+'\n')
            process.stdin.flush(); sleep(1)
            for fixture in windows:
                hwnd = int(fixture.winId())
                user32.SetForegroundWindow(hwnd); sleep(.3)
                for overlay in shared['hwnds']:
                    user32.SetWindowPos(overlay, wintypes.HWND(-1), 0, 0, 0, 0, 0x13)
                sleep(.2)
                width, height = fixture.width(), fixture.height()
                for x, y in ((10,height//2),(width-10,height//2),(width//2,10),(width//2,height-10)):
                    gx, gy = fixture.x()+x, fixture.y()+y
                    if int(user32.WindowFromPoint(wintypes.POINT(gx, gy)) or 0) != hwnd:
                        raise RuntimeError('Own target occluded; further input skipped')
                    user32.SetCursorPos(gx, gy)
                    user32.mouse_event(2,0,0,0,0); user32.mouse_event(4,0,0,0,0)
                    sleep(.08)
                    user32.mouse_event(0x800,0,0,120,0); sleep(.1)
            report['delivered'] = len(events)
            report['valid_delivery_count'] = sum(e['overlay_above_fixture'] and
                e['foreground_hwnd'] == e['fixture_hwnd'] for e in events)
            report['status'] = 'PASS' if report['valid_delivery_count'] == len(windows)*8 else 'PARTIAL'
        except Exception as exc:
            report['status'], report['error'] = 'UNVERIFIED', repr(exc)
        finally:
            if process:
                process.stdin.close()
                try: process.wait(5)
                except subprocess.TimeoutExpired: process.terminate(); process.wait(3)
                report['renderer_exit_code'] = process.returncode
                report['renderer_stderr'] = process.stderr.read()
            report['renderer_packets'] = shared['packets']
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            finished.set()
    QTimer.singleShot(150, lambda: Thread(target=run, daemon=True).start())
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if finished.is_set() else None)
    timer.start(100)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
