"""Bounded IPC for the existing Qt renderer; no render work on voice threads."""
from contextlib import contextmanager
from collections import Counter, deque
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Lock, RLock, Thread
from time import perf_counter
import uuid

from . import capture_guard
from .edge_model import voice_energy


def visual_packet(state, config):
    """Project inline Live's transient visibility without changing user prefs."""
    status = state.state.value
    visual = state.visual_snapshot()
    visual['user_visual_hidden'] |= (not config.get('ui.overlay', True)
                                    or visual.get('desktop_inline_live', False))
    return dict(state=status, visible=not visual['user_visual_hidden'],
        active=getattr(state, 'conversation_active', False), **visual,
        amplitude=(0. if config.get('ui.edge_calm_mode', True) else
                   voice_energy(status, getattr(state, 'audio_level', 0), getattr(state, 'tts_level', 0))),
        simplified=bool(getattr(state, 'graphics_simplified', False)))


def renderer_process_owned(renderer_pid, launcher_pid):
    """Accept our process or its venv launcher child, never an unrelated PID."""
    import psutil
    try:
        renderer = psutil.Process(int(renderer_pid))
        launcher = psutil.Process(int(launcher_pid))
        return (renderer.create_time() >= launcher.create_time() and
                (renderer.pid == launcher.pid or
                 any(parent.pid == launcher.pid for parent in renderer.parents())))
    except (psutil.Error, TypeError, ValueError):
        return False


class CaptureHandshake:
    def __init__(self, send, timeout=1.5):
        self.send, self.timeout = send, timeout
        self.lock = RLock()
        self.pending_lock = Lock()
        self.pending = {}
        self.counters = Counter()
        self.timeline = deque(maxlen=96)
        self.renderer_identity = {}

    def record(self, event, **details):
        with self.pending_lock:
            self.counters[event] += 1
            self.timeline.append({'monotonic': perf_counter(), 'event': event, **details})

    def defer_ambient(self):
        self.record('ambient_deferred', reason='unverified_capture_exclusion')

    def diagnostics(self):
        with self.pending_lock:
            return {'counters': dict(self.counters), 'timeline': list(self.timeline),
                    'exclusion_pixel_verified': False,
                    'renderer_identity': dict(self.renderer_identity),
                    'explicit_capture_policy': 'hide_then_dwmflush'}

    def acknowledge(self, packet):
        with self.pending_lock:
            entry = self.pending.get(packet.get('capture_ack'))
        if entry:
            entry[1].update(packet)
            entry[0].set()

    def request(self, hidden):
        token, event, response = uuid.uuid4().hex, Event(), {}
        self.record('hide_request' if hidden else 'restore_request', token=token)
        with self.pending_lock:
            self.pending[token] = (event, response)
        try:
            self.send({'control': 'capture', 'hidden': hidden, 'token': token})
            if not event.wait(self.timeout) or not response.get('ok'):
                self.record('capture_ack_failed', token=token, hidden=hidden)
                raise RuntimeError('capture/exclusion_failed: renderer composition acknowledgement unavailable')
            self.record('hide_ack' if hidden else 'restore_ack', token=token)
        finally:
            with self.pending_lock:
                self.pending.pop(token, None)

    @contextmanager
    def capture_guard(self):
        with self.lock:
            started = False
            try:
                self.request(True)
                self.record('capture_begin')
                started = True
                yield
            finally:
                if started:
                    self.record('capture_end')
                # Even a timeout may mean hide arrived. Always attempt restore.
                try:
                    self.request(False)
                except (OSError, RuntimeError):
                    logging.getLogger(__name__).warning('Edge restore not acknowledged', exc_info=True)


def run(overlay):
    config, state = overlay.config, overlay.state
    root = config.root
    compatibility = config.get('ui.orb_compatibility', False)
    module = 'kk.ui.gpu_orb' if compatibility else 'kk.ui.gpu_edge'
    env = dict(os.environ, PYTHONUTF8='1', QSG_RHI_BACKEND='d3d11', QSG_RENDER_LOOP='threaded')
    executable = Path(sys.executable).with_name('pythonw.exe')
    if not executable.exists(): executable = Path(sys.executable)
    process = subprocess.Popen([str(executable), '-m', module, str(root)], cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding='utf-8', bufsize=1, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    overlay.process = process
    logging.getLogger('kk').info('[EDGE] pid=%s parent=%s executable=%s cwd=%s compatibility=%s',
                                 process.pid, os.getpid(), executable, root, compatibility)
    write_lock = Lock()

    def send(packet):
        with write_lock:
            process.stdin.write(json.dumps(packet)+'\n')
            process.stdin.flush()

    handshake = CaptureHandshake(send)
    overlay.capture_handshake = handshake
    renderer_pid = None

    def read():
        nonlocal renderer_pid
        for line in process.stdout:
            try:
                packet = json.loads(line)
                if 'renderer_identity' in packet:
                    handshake.renderer_identity = packet['renderer_identity']
                    overlay.renderer_identity = packet['renderer_identity']
                if 'capture_ack' in packet:
                    handshake.acknowledge(packet)
                if 'hwnds' in packet:
                    candidate = packet.get('renderer_pid')
                    if candidate != renderer_pid:
                        if not renderer_process_owned(candidate, process.pid):
                            raise RuntimeError('Renderer HWND registration has no matching launcher ancestry')
                        renderer_pid = candidate
                    overlay.hwnds = packet['hwnds']
                    overlay.renderer_pid = renderer_pid
                    capture_guard.update_windows(handshake, overlay.hwnds, renderer_pid)
                if 'hwnd' in packet: overlay.hwnd = packet['hwnd']
                if 'position' in packet: overlay.position = tuple(packet['position'])
                if 'monitor' in packet: overlay.monitor_id = packet['monitor']
                if 'metrics' in packet:
                    overlay.metrics = packet['metrics']
                    logging.getLogger('kk').debug('[EDGE] metrics=%s', packet['metrics'])
                if 'capabilities' in packet:
                    overlay.capabilities = getattr(overlay, 'capabilities', {})
                    overlay.capabilities[packet['capabilities']['hwnd']] = packet['capabilities']
                    logging.getLogger('kk').info('[EDGE] capabilities=%s', packet['capabilities'])
                if 'lifecycle' in packet:
                    overlay.lifecycle = packet['lifecycle']
                action = packet.get('action')
                if overlay.microphone and action in {'mic_toggle','mic_start','mic_commit','mic_cancel'}:
                    getattr(overlay.microphone, action)()
                    performance = getattr(overlay.microphone, 'performance', None)
                    if performance and 'at' in packet:
                        performance.record('mic_input_ipc', perf_counter()-packet['at'], action=action)
            except Exception:
                logging.getLogger(__name__).exception('Renderer IPC callback failed')

    def errors():
        for line in process.stderr:
            logging.getLogger(__name__).warning('Qt renderer: %s', line.rstrip())

    if not compatibility:
        capture_guard.register(handshake)
    Thread(target=read, daemon=True, name='KKEdgeEvents').start()
    Thread(target=errors, daemon=True, name='KKEdgeDiagnostics').start()
    previous = None
    try:
        while not overlay.stop_event.wait(1/30):
            if process.poll() is not None:
                raise RuntimeError('Qt renderer exited: '+str(process.returncode))
            packet = visual_packet(state, config)
            if packet != previous:
                send(packet)
                previous = packet
    finally:
        try:
            try:
                process.stdin.close()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                # The venv launcher is not the HWND-owning process. End only
                # its positively identified renderer child before the launcher,
                # otherwise a stuck Qt process could become an orphan.
                if renderer_pid and renderer_pid != process.pid and renderer_process_owned(renderer_pid, process.pid):
                    import psutil
                    try:
                        renderer = psutil.Process(renderer_pid)
                        renderer.terminate()
                        renderer.wait(timeout=2)
                    except psutil.Error:
                        logging.getLogger(__name__).warning('Renderer child shutdown incomplete', exc_info=True)
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=3)
        finally:
            # Until the HWNDs are gone, a concurrent fresh capture must still
            # negotiate exclusion (or fail), including during shutdown.
            capture_guard.unregister(handshake)
        overlay.hwnd = None
        overlay.hwnds = []
