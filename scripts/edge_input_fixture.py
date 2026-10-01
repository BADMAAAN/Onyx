"""Own test surfaces for real edge clicks/scroll; never sends input itself.

Run only under the coordinating desktop tester. Every monitor gets a frameless
fixture with 40 logical-pixel target bands and a visible event readout. The JSON
is evidence of input received by the fixture, not a simulated pass from flags.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
from time import monotonic

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=90)
    parser.add_argument('--output', type=Path, default=Path('data/master_live/edge_input_fixture.json'))
    parser.add_argument('--verify-capture', action='store_true', help='Own fixture + existing production Edge/capture; stop background first')
    args = parser.parse_args()
    app = QApplication(sys.argv)
    started, events, windows = monotonic(), [], []
    user32 = ctypes.WinDLL('user32')
    user32.GetForegroundWindow.restype = wintypes.HWND
    args.output.parent.mkdir(parents=True, exist_ok=True)
    capture_result = {}

    def save():
        data = dict(source='real Qt fixture mousePressEvent/wheelEvent', seconds=monotonic()-started,
                    test_automation=True, capture=capture_result,
                    windows=[dict(monitor=w.monitor_name, hwnd=int(w.winId()),
                                  logical_bounds=[w.x(), w.y(), w.width(), w.height()],
                                  dpr=w.devicePixelRatioF()) for w in windows], events=events)
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    class Fixture(QWidget):
        def __init__(self, screen):
            super().__init__()
            self.monitor_name = screen.name()
            self.setWindowTitle('Onyx Edge Input Fixture — '+self.monitor_name)
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setGeometry(screen.geometry())

        def record(self, kind, event):
            p = event.position()
            distances = {'left': p.x(), 'right': self.width()-p.x(),
                         'top': p.y(), 'bottom': self.height()-p.y()}
            edge = min(distances, key=distances.get)
            row = dict(kind=kind, monitor=self.monitor_name, side=edge,
                       in_edge_band=distances[edge] <= 40,
                       logical_point=[p.x(), p.y()], global_point=[event.globalPosition().x(), event.globalPosition().y()],
                       foreground_hwnd=int(user32.GetForegroundWindow() or 0),
                       fixture_hwnd=int(self.winId()), seconds=monotonic()-started)
            if kind == 'scroll': row['angle_delta_y'] = event.angleDelta().y()
            events.append(row)
            save()
            for window in windows: window.update()
            event.accept()

        def mousePressEvent(self, event):
            self.record('click', event)

        def wheelEvent(self, event):
            self.record('scroll', event)

        def keyPressEvent(self, event):
            if event.key() == Qt.Key_Escape: app.quit()
            else: super().keyPressEvent(event)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#17202b'))
            painter.setPen(QColor('#a9bfd3'))
            painter.setFont(QFont('Segoe UI', 13))
            painter.fillRect(0, 0, self.width(), 40, QColor('#314256'))
            painter.fillRect(0, self.height()-40, self.width(), 40, QColor('#314256'))
            painter.fillRect(0, 40, 40, self.height()-80, QColor('#314256'))
            painter.fillRect(self.width()-40, 40, 40, self.height()-80, QColor('#314256'))
            painter.drawText(self.rect().adjusted(70, 80, -70, -80), Qt.AlignCenter,
                'Onyx Edge input fixture\n'+self.monitor_name+
                '\nClick and scroll within 40 px of each edge.\nEsc closes only this fixture.\n\n'+
                '\n'.join(f"{e['monitor']} / {e['side']} / {e['kind']} / edge={e['in_edge_band']}"
                          for e in events[-10:]))

    for screen in app.screens():
        window = Fixture(screen)
        windows.append(window)
        window.show()
    if args.verify_capture:
        from kk.config import Config
        from kk.core.state import StateMachine, AgentState
        from kk.ui.overlay import Overlay
        from kk.ui.capture_guard import clean_capture
        from kk.vision.screen_capture import ScreenCapture
        from PIL import ImageChops
        root = Path(__file__).resolve().parents[1]
        if (root/'data/runtime_control.json').exists():
            raise RuntimeError('Stop background Onyx before starting this isolated renderer probe')
        state = StateMachine()  # no user preference mutations
        state.activate_visual('test_fixture')
        state.set(AgentState.LISTENING)
        state.set_visual_visible(False)
        overlay = Overlay(state, Config.load(root/'config.toml'))
        overlay.start()
        capture = ScreenCapture()
        baseline = {}
        def shots():
            # Every region is a full own fixture surface; never capture browsers.
            return {w.monitor_name: capture.capture_image('all',
                [w.x(), w.y(), w.x()+w.width(), w.y()+w.height()], 32768)[0] for w in windows}
        def begin():
            try:
                baseline.update(shots())
                for image in baseline.values():
                    points = [(10,image.height//2),(image.width-10,image.height//2),
                              (image.width//2,10),(image.width//2,image.height-10)]
                    if any(image.getpixel(point) != (49,66,86) for point in points):
                        raise RuntimeError('Fixture border is occluded; comparison is invalid')
                state.set_visual_visible(True)
                QTimer.singleShot(2200, compare)
            except Exception as exc:
                capture_result['error'] = str(exc); save()
        def compare():
            try:
                current = shots()
                rows = []
                for name, image in current.items():
                    delta = ImageChops.difference(baseline[name], image)
                    borders = [(10,image.height//2),(image.width-10,image.height//2),
                               (image.width//2,10),(image.width//2,image.height-10)]
                    rows.append(dict(monitor=name, max_difference=max(v[1] for v in delta.getextrema()),
                                     edge_pixels=[image.getpixel(point) for point in borders]))
                    delta.close(); image.close(); baseline[name].close()
                capture_result['off_on'] = rows
                try:
                    with clean_capture():
                        raise RuntimeError('intentional fixture capture failure')
                except RuntimeError:
                    capture_result['injected_error'] = True
                QTimer.singleShot(2600, restored)
            except Exception as exc:
                capture_result['error'] = str(exc); save()
        def restored():
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            capture_result['visible_after_error'] = {
                str(hwnd): bool(user32.IsWindowVisible(hwnd)) for hwnd in getattr(overlay, 'hwnds', [])}
            capture_result['renderer_metrics_after_error'] = getattr(overlay, 'metrics', [])
            capture_result['renderer_pid'] = getattr(overlay, 'renderer_pid', None)
            capture_result['ready_for_input'] = True
            save()
        def stop_overlay():
            overlay.stop()
            if overlay.thread: overlay.thread.join(5)
            capture_result['renderer_stopped'] = not overlay.thread.is_alive()
            save()
        app.aboutToQuit.connect(stop_overlay)
        QTimer.singleShot(2200, begin)
    save()
    app.aboutToQuit.connect(save)
    QTimer.singleShot(max(1, args.seconds)*1000, app.quit)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
