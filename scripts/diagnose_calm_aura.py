"""Isolated real D3D/Pillow aura probe on opaque, owned static surfaces only.

The backend must be stopped by its coordinator. This never opens a browser or
reads browser metadata; renderer affinity toggles are gated by --probe. Evidence
is local JSON/PNGs and all native fixture/renderer processes close afterward.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock, Thread
from time import monotonic, sleep
import uuid

from PIL import Image, ImageChops, ImageGrab
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from kk.ambient.manager import AmbientContextManager
from kk.ui import capture_guard
from kk.ui.gpu_bridge import CaptureHandshake

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'data/repair_speech_targets/aura_probe.json'


def main():
    with_tts = '--tts' in sys.argv
    output = OUTPUT.with_name('aura_probe_tts.json') if with_tts else OUTPUT
    if (ROOT/'data/runtime_control.json').exists():
        raise RuntimeError('Coordinator must stop Onyx before this isolated probe')
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetTopWindow.argtypes = [wintypes.HWND]
    user32.GetTopWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    report = {'source': 'isolated production Qt/D3D11 + Pillow.ImageGrab + AmbientContextManager',
              'test_automation': True, 'human_perceived_flicker': 'UNVERIFIED',
              'ordinary_human_voice': 'UNVERIFIED', 'stages': [], 'events': []}
    windows, process, bridge = [], None, None
    finished = Event()

    def z_order():
        ordered = []
        handle = user32.GetTopWindow(None)
        own = {int(window.winId()) for window in windows} | set(shared.get('hwnds', []))
        rank = 0
        while handle:
            if int(handle) in own:
                ordered.append({'hwnd': int(handle), 'rank': rank,
                                'visible': bool(user32.IsWindowVisible(handle))})
            handle = user32.GetWindow(handle, 2)
            rank += 1
        return ordered

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    class Fixture(QWidget):
        def __init__(self, screen):
            super().__init__()
            self.monitor_name = screen.name()
            self.setWindowTitle('Onyx Calm Static Diagnostic')
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setGeometry(screen.geometry())
        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#314256'))
        def mousePressEvent(self, event):
            report['events'].append({'kind': 'click', 'monitor': self.monitor_name,
                'foreground_hwnd': int(user32.GetForegroundWindow() or 0),
                'fixture_hwnd': int(self.winId()), 'z_order': z_order(),
                'point': [event.position().x(), event.position().y()]})
            event.accept()
        def wheelEvent(self, event):
            report['events'].append({'kind': 'scroll', 'monitor': self.monitor_name,
                'foreground_hwnd': int(user32.GetForegroundWindow() or 0),
                'fixture_hwnd': int(self.winId()), 'z_order': z_order(),
                'point': [event.position().x(), event.position().y()]})
            event.accept()

    for screen in app.screens():
        window = Fixture(screen)
        windows.append(window)
        window.show()
    shared = {'hwnds': [], 'metrics': [], 'packets': [], 'stderr': []}
    windows[-1].activateWindow()
    rectangles = []
    for index, window in enumerate(windows):
        # QWidget geometries are logical; these probes require physical 100%
        # DPI surfaces. Other DPI remains explicitly unverified.
        if window.devicePixelRatioF() != 1:
            raise RuntimeError('Probe requires 100% DPI; mixed DPI remains unverified')
        rectangles.append({'monitor_id': window.monitor_name, 'index': index,
            'left': window.x(), 'top': window.y(),
            'right': window.x()+window.width(), 'bottom': window.y()+window.height()})
    report['monitors'] = rectangles
    report['fixture_hwnds'] = [int(window.winId()) for window in windows]

    def worker():
        nonlocal process, bridge
        manager = None
        baselines = {}
        try:
            sleep(.8)
            # Show opaque owned surfaces first, then launch exactly one renderer.
            for row in rectangles:
                box = tuple(row[key] for key in ('left', 'top', 'right', 'bottom'))
                image = ImageGrab.grab(bbox=box, all_screens=True).convert('RGB')
                target = Image.new('RGB', image.size, (49, 66, 86))
                diff = ImageChops.difference(image, target)
                if diff.getbbox() is not None:
                    raise RuntimeError('Own fixture is occluded; no screenshots saved')
                target.close(); diff.close()
                baselines[row['monitor_id']] = image
            process = subprocess.Popen([sys.executable, '-m', 'kk.ui.gpu_edge', str(ROOT), '--probe'],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', bufsize=1,
                env=dict(os.environ, PYTHONUTF8='1'), creationflags=subprocess.CREATE_NO_WINDOW)
            lock = Lock()
            def send(packet):
                with lock:
                    process.stdin.write(json.dumps(packet)+'\n'); process.stdin.flush()
            bridge = CaptureHandshake(send)
            capture_guard.register(bridge)
            def read():
                for line in process.stdout:
                    packet = json.loads(line)
                    shared['packets'].append(packet)
                    for key in ('hwnds', 'metrics', 'metrics_monotonic', 'lifecycle', 'renderer_identity'):
                        if key in packet: shared[key] = packet[key]
                    if 'renderer_identity' in packet: bridge.renderer_identity = packet['renderer_identity']
                    if 'hwnds' in packet:
                        capture_guard.update_windows(bridge, packet['hwnds'], packet['renderer_pid'])
                    if 'capture_ack' in packet: bridge.acknowledge(packet)
            Thread(target=read, daemon=True).start()
            def stderr():
                shared['stderr'].extend(process.stderr.readlines())
            Thread(target=stderr, daemon=True).start()
            deadline = monotonic()+10
            while len(shared['hwnds']) != len(windows) and monotonic() < deadline: sleep(.05)
            if len(shared['hwnds']) != len(windows): raise RuntimeError('Renderer topology incomplete')
            original_hwnds = list(shared['hwnds'])
            def raw():
                return {row['monitor_id']: ImageGrab.grab(bbox=tuple(row[k] for k in ('left','top','right','bottom')),
                    all_screens=True).convert('RGB') for row in rectangles}
            def delta(images, reference):
                rows = []
                for name, image in images.items():
                    diff = ImageChops.difference(image, reference[name])
                    bounds = diff.getbbox()
                    center = diff.crop((96,96,image.width-96,image.height-96))
                    rows.append({'monitor': name, 'max_difference': max(v[1] for v in diff.getextrema()),
                                 'changed_bounds': bounds, 'center_difference': max(v[1] for v in center.getextrema())})
                    center.close(); diff.close()
                return rows
            def snap(stage, **data):
                # Get fresh counters even when hidden report timers are stopped.
                previous_metrics = shared.get('metrics_monotonic')
                send({'control': 'diagnostics'})
                deadline = monotonic()+2
                while shared.get('metrics_monotonic') == previous_metrics and monotonic() < deadline:
                    sleep(.01)
                report['stages'].append({'stage': stage, 'monotonic': monotonic(),
                    'capture': bridge.diagnostics(), 'renderer_metrics': list(shared['metrics']),
                    'lifecycle': shared.get('lifecycle'), 'z_order': z_order(),
                    'foreground_hwnd': int(user32.GetForegroundWindow() or 0), **data})
                save()
            def affinity(enabled):
                token, done, response = uuid.uuid4().hex, Event(), {}
                with bridge.pending_lock: bridge.pending[token] = (done, response)
                try:
                    send({'control': 'probe_affinity', 'enabled': enabled, 'token': token})
                    if not done.wait(3) or not response.get('ok'): raise RuntimeError('Owner affinity diagnostic failed')
                finally:
                    with bridge.pending_lock: bridge.pending.pop(token, None)
                sleep(.25)
            packet = dict(state='IDLE', active=False, user_visual_hidden=False, activation_serial=0, amplitude=0)
            send(packet); sleep(1)
            snap('aura_off', pixels=delta(raw(), baselines))
            packet.update(state='LISTENING', active=True, activation_serial=1)
            send(packet); sleep(1.5)
            # Measure genuine raw WDA exclusion before temporarily disabling it.
            snap('static_on_wda_enabled', pixels=delta(raw(), baselines))
            affinity(False)
            visual = raw()
            visual_delta = delta(visual, baselines)
            if any(row['max_difference'] < 3 or row['center_difference'] for row in visual_delta):
                raise RuntimeError('No clean, visible diffuse aura on every own surface')
            for index, image in enumerate(visual.values()):
                image.save(OUTPUT.with_name('aura_visible_monitor_'+str(index+1)+'.png'))
            snap('static_on_capture_off', pixels=visual_delta)
            sleep(2.3)
            snap('static_settled', pixels=delta(raw(), visual))
            manager = AmbientContextManager(OUTPUT.with_name('aura_probe_ambient.db'))
            # Several actual production ambient sampling calls must never invoke
            # hide/show, even while affinity is intentionally disabled for pixels.
            before = bridge.diagnostics()['counters'].copy()
            for _ in range(5):
                manager._screen_sample(rectangles)
                sleep(.3)
            snap('ambient_capture_on', pixels=delta(raw(), visual),
                 before=before, screen_samples=manager.screen_samples,
                 screen_deferred=manager.screen_deferred,
                 screen_deferred_reason=manager.screen_deferred_reason)
            for amplitude in (0., .9, .1, 1., 0.):
                packet.update(state='SPEAKING', amplitude=amplitude)
                send(packet); sleep(.25)
            snap('speaking_state_amplitude_events', pixels=delta(raw(), visual),
                 note='Production renderer events; actual TTS/audio checked separately')
            if with_tts:
                # Actual production synthesis + PortAudio playback. The GUI
                # thread keeps painting the same owned backgrounds; no mic is
                # opened, and no user/browser surface is sampled.
                from types import SimpleNamespace
                from kk.config import Config
                from kk.core.state import StateMachine, AgentState
                from kk.core.task import CancellationToken
                from kk.providers.resources import ModelResourceManager, PressureGuard, available_vram
                from kk.voice.voices import build_tts
                from kk.voice.speech_queue import SpeechQueue
                from kk.ui.edge_model import voice_energy
                config = Config.load(ROOT/'config.toml')
                resources = ModelResourceManager(SimpleNamespace(
                    base_url=config.get('providers.ollama.base_url', 'http://127.0.0.1:11434')),
                    config.get('resources.ram_reserve_gb', 8),
                    config.get('resources.stt_vram_gb', 2),
                    config.get('resources.vram_reserve_gb', 2.5))
                resources.prepare_tts()
                resource_before = resources.snapshot()
                if available_vram(resource_before) < resources.vram_reserve:
                    raise RuntimeError('Configured VRAM reserve unavailable before TTS')
                tts = build_tts(config)
                if tts.muted or tts.volume <= 0:
                    raise RuntimeError('Playback disabled by current config; actual TTS unverified')
                tts.before_load = resources.prepare_tts
                state = StateMachine()
                state.activate_visual('own_surface_tts_probe')
                state.conversation_active = True
                state.set(AgentState.LISTENING)
                speech = SpeechQueue(tts, state)
                token = CancellationToken(diagnostic=True)
                guard = PressureGuard(resources, 'silero_cpu_aura_probe')
                guard.abort_io = lambda: speech.cancel(reason='resource_pressure', source='aura_probe')
                sentence = 'Спокойное свечение остаётся неподвижным, пока я говорю эту короткую фразу.'
                observations = []
                explicit_during_audio = None
                report['tts'] = {'text': sentence, 'microphone_opened': False,
                    'resource_before': resource_before,
                    'ram_reserve_bytes': resources.ram_reserve,
                    'vram_reserve_bytes': resources.vram_reserve,
                    'status': 'UNVERIFIED'}
                try:
                    guard.thread.start()
                    speech.begin(token)
                    speech.submit(sentence, token)
                    speech.finish(token)
                    deadline, next_sample, previous_packet = monotonic()+90, 0., None
                    while not speech.done.is_set():
                        guard.check(); token.check()
                        if monotonic() > deadline:
                            raise TimeoutError('Actual TTS did not finish within 90 seconds')
                        status = state.state.value
                        actual_packet = dict(state=status, active=state.conversation_active,
                            **state.visual_snapshot(), amplitude=(0. if config.get('ui.edge_calm_mode', True)
                                else voice_energy(status, playback=getattr(state, 'tts_level', 0.))))
                        if actual_packet != previous_packet:
                            send(actual_packet)
                            previous_packet = actual_packet
                        if speech.playing.is_set() and speech.audio_started.is_set() and monotonic() >= next_sample:
                            started_sample = monotonic()
                            frames = raw()
                            pixels = delta(frames, visual)
                            for frame in frames.values(): frame.close()
                            manager._screen_sample(rectangles)
                            observations.append({'monotonic_start': started_sample,
                                'monotonic_end': monotonic(), 'pixels': pixels,
                                'playing_after_capture': speech.playing.is_set(),
                                'tts_level': getattr(state, 'tts_level', 0.)})
                            if explicit_during_audio is None and speech.playing.is_set():
                                from kk.vision.screen_capture import ScreenCapture
                                explicit_start = monotonic()
                                explicit_frames = {}
                                for row in rectangles:
                                    explicit_frames[row['monitor_id']] = ScreenCapture().capture_image('all',
                                        [row[k] for k in ('left','top','right','bottom')], 32768)[0]
                                explicit_during_audio = {'monotonic_start': explicit_start,
                                    'monotonic_end': monotonic(), 'pixels': delta(explicit_frames, baselines),
                                    'playing_after_capture': speech.playing.is_set()}
                                for frame in explicit_frames.values(): frame.close()
                            next_sample = monotonic()+.2
                        sleep(.02)
                    speech.wait(token, timeout=1)
                    guard.check()
                    completed = [event for event in token.trace if event['event'] == 'audio_playback_complete']
                    all_drained = bool(completed and any(event['event'] == 'speech_complete' for event in token.trace)
                        and all(event['completed_frames'] == event['submitted_frames'] > 0 for event in completed))
                    verified_samples = [row for row in observations if row['playing_after_capture']]
                    stable = bool(verified_samples and all(pixel['max_difference'] == 0
                        for row in verified_samples for pixel in row['pixels']))
                    clean_during_audio = bool(explicit_during_audio and
                        explicit_during_audio['playing_after_capture'] and
                        all(pixel['max_difference'] == 0 for pixel in explicit_during_audio['pixels']))
                    underflows = sum(event.get('underflows', 0) for event in completed)
                    report['tts'].update(status='PASS' if all_drained and stable and clean_during_audio
                        and not underflows else 'PARTIAL',
                        all_audio_drained=all_drained, stable_during_audio=stable,
                        explicit_clean_during_audio=clean_during_audio, underflows=underflows,
                        complete_overlap_samples=len(verified_samples))
                    snap('actual_tts_drained', pixels=delta(raw(), visual),
                         all_audio_drained=all_drained, overlap_samples=len(verified_samples))
                finally:
                    guard.done.set(); guard.thread.join(timeout=3)
                    speech.close()
                    report['tts'].update(trace=list(token.trace), observations=observations,
                        explicit_during_audio=explicit_during_audio,
                        loaded_tts=tts.runtime_diagnostics(), resource_samples=list(guard.samples),
                        pressure_aborted=guard.aborted.is_set(), pressure_reason=guard.reason,
                        resource_after=resources.snapshot())
                    save()
            # Input is injected only into these owned fixtures. Recheck hit
            # ownership before each action and record actual z-order at delivery.
            for window, row in zip(windows, rectangles):
                fixture_hwnd = int(window.winId())
                user32.SetForegroundWindow(fixture_hwnd)
                # Foreground activation itself may reorder topmost windows.
                # Wait for that operation before explicitly raising the aura.
                sleep(.3)
                for hwnd in shared['hwnds']:
                    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x13)
                sleep(.15)
                ranks = {item['hwnd']: item['rank'] for item in z_order()}
                if any(ranks[hwnd] >= ranks[fixture_hwnd] for hwnd in shared['hwnds']):
                    raise RuntimeError('Fixture overlays the aura; input evidence would be invalid')
                width, height = row['right']-row['left'], row['bottom']-row['top']
                for x, y in ((10,height//2),(width-10,height//2),(width//2,10),(width//2,height-10)):
                    gx, gy = row['left']+x, row['top']+y
                    if int(user32.WindowFromPoint(wintypes.POINT(gx, gy)) or 0) != fixture_hwnd:
                        raise RuntimeError('Own input target is occluded; input skipped')
                    user32.SetCursorPos(gx, gy)
                    user32.mouse_event(0x02, 0, 0, 0, 0)
                    user32.mouse_event(0x04, 0, 0, 0, 0)
                    sleep(.06)
                    user32.mouse_event(0x0800, 0, 0, 120, 0)
                    sleep(.08)
            snap('input_fixture', delivered=len(report['events']),
                 note='Own Win32 mouse injection, actual Qt handler delivery; see per-event z-order')
            # Explicit clean capture retains the bounded fallback and real pixel
            # verification even when WDA is intentionally disabled.
            from kk.vision.screen_capture import ScreenCapture
            capture = ScreenCapture()
            clean = {}
            for row in rectangles:
                clean[row['monitor_id']] = capture.capture_image('all',
                    [row[k] for k in ('left','top','right','bottom')], 32768)[0]
            snap('explicit_screenshot', pixels=delta(clean, baselines))
            try:
                with capture_guard.clean_capture(): raise RuntimeError('injected probe failure')
            except RuntimeError: pass
            sleep(1)
            snap('explicit_exception_restored', pixels=delta(raw(), visual))
            affinity(True)
            snap('wda_reenabled', pixels=delta(raw(), baselines))
            packet.update(user_visual_hidden=True)
            send(packet); sleep(1.2)
            snap('manual_hidden', pixels=delta(raw(), baselines))
            sleep(2)
            snap('hidden_settled', same_hwnds=original_hwnds == shared['hwnds'])
            report['identity'] = shared.get('renderer_identity')
            report['capabilities'] = [p['capabilities'] for p in shared['packets'] if 'capabilities' in p]
            report['status'] = 'MEASURED'
        except Exception as exc:
            report['status'] = 'FAILED'
            report['error'] = repr(exc)
        finally:
            if manager: manager.stop()
            if process:
                try:
                    process.stdin.close(); process.wait(5)
                except Exception:
                    process.terminate(); process.wait(3)
                report['renderer_exit_code'] = process.returncode
            if bridge: capture_guard.unregister(bridge)
            report['renderer_stderr'] = shared['stderr']
            for image in baselines.values(): image.close()
            save()
            finished.set()
    QTimer.singleShot(150, lambda: Thread(target=worker, daemon=True).start())
    shutdown = QTimer()
    shutdown.timeout.connect(lambda: app.quit() if finished.is_set() else None)
    shutdown.start(100)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
