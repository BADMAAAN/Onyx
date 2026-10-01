"""Edge renderer supervisor; the historical Orb is an explicit compatibility mode."""
from threading import Event, Thread
from time import monotonic
import logging

from kk.core.state import AgentState
from .orb_model import (select_monitor, placement, load_positions, save_positions,
                        is_fullscreen, hit_orb)
from .orb_renderer import OrbRenderer


class Overlay:
    def __init__(self, state, config=None, microphone=None):
        self.state, self.config = state, config
        self.stop_event = Event()
        self.thread = None
        self.hwnd = None
        self.position = (0, 0)
        self.monitor_id = None
        self.microphone = microphone

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = Thread(target=self._run, name='KKOverlay', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        get = self.config.get if self.config else lambda key, default: default
        compatibility = bool(get('ui.orb_compatibility', False))
        if not compatibility or get('ui.orb_renderer', 'qtquick') == 'qtquick':
            try:
                from .gpu_bridge import run
                return run(self)
            except Exception:
                logging.getLogger(__name__).exception('Qt renderer unavailable; voice and tray remain active')
                if not compatibility:
                    # A rendering fault must not spawn an unwanted Orb or shut
                    # down tray/voice. Re-launch remains an explicit recovery.
                    self.stop_event.wait()
                    return
        from .orb_window import OrbWindow, foreground_info
        from kk.tools.windows.native import list_monitors
        get = self.config.get if self.config else lambda key, default: default
        size = max(80, min(320, int(get('ui.orb_size', 144))))
        fps = max(15, min(60, int(get('ui.orb_fps', 30))))
        path = self.config.root/'data'/'orb_positions.json' if self.config else None
        positions = load_positions(path)
        renderer = OrbRenderer(size)
        monitors, last_topology, last_foreground = [], -10., -10.
        fullscreen, foreground, visible_before = False, {}, False
        last = monotonic()
        dragging = None
        drag_monitor = None
        drag_origin = None
        from .mic_pointer import MicPointer, mic_hit
        mic = self.microphone
        noop = lambda: None
        pointer = MicPointer(mic.mic_toggle if mic else noop, mic.mic_start if mic else noop,
                             mic.mic_commit if mic else noop, mic.mic_cancel if mic else noop)

        def persist():
            positions[self.monitor_id] = list(self.position)
            try:
                save_positions(path, positions)
            except OSError:
                logging.getLogger(__name__).warning('Cannot save Orb position', exc_info=True)

        def mouse(event, point):
            nonlocal dragging, drag_monitor, drag_origin
            if event == 'right':
                if mic: mic.mic_cancel()
                return
            if event == 'down' and mic_hit(point[0]-self.position[0], point[1]-self.position[1], size):
                pointer.press(point, monotonic())
                return
            if pointer.down:
                if event == 'move': pointer.move(point)
                elif event in {'up','cancel'}: pointer.release(point, event == 'cancel')
                return
            if event == 'down':
                dragging = (point[0]-self.position[0], point[1]-self.position[1])
                drag_origin = point
                drag_monitor = self.monitor_id
            elif event == 'move' and dragging:
                if drag_origin and ((point[0]-drag_origin[0])**2+(point[1]-drag_origin[1])**2)**.5 < 6:
                    return
                drag_origin = None
                self.position = (point[0]-dragging[0], point[1]-dragging[1])
                window.move(*self.position)
            elif event in {'up', 'cancel'} and dragging:
                dragging = None
                chosen = select_monitor(monitors, cursor=(self.position[0]+size//2, self.position[1]+size//2))
                if chosen:
                    self.monitor_id = chosen['monitor_id']
                    self.position = placement(chosen, (size, size), self.position)
                    persist()
                    window.move(*self.position)

        window = OrbWindow(size, mouse, lambda x, y: hit_orb(x, y, size))
        self.hwnd = window.hwnd
        try:
            while not self.stop_event.is_set() and window.pump():
                now = monotonic()
                if pointer.down:
                    pointer.move(window.cursor())
                    pointer.tick(now)
                    if not window.left_button_down():
                        pointer.release(window.cursor())
                        window.release_capture()
                dt, last = now-last, now
                if now-last_topology >= 2:
                    monitors = list_monitors()
                    last_topology = now
                requested = self.state.orb_visible and get('ui.overlay', True)
                # Background/no-activate HWND capture can lose events over other
                # processes. Continue a drag that started on our disc by polling
                # its button until release, without ever taking keyboard focus.
                if dragging:
                    mouse('move', window.cursor())
                    if not window.left_button_down():
                        mouse('up', window.cursor())
                        window.release_capture()
                if now-last_foreground >= .3:
                    foreground = foreground_info()
                    fg_monitor = select_monitor(monitors, cursor=foreground.get('center'))
                    fullscreen = bool(get('ui.fullscreen_auto_hide', True) and is_fullscreen(foreground, fg_monitor))
                    last_foreground = now
                if monitors and (self.monitor_id is None or not any(m['monitor_id'] == self.monitor_id for m in monitors)):
                    saved_monitor = positions.get('_last_monitor')
                    chosen = select_monitor(monitors, saved_monitor,
                        window.cursor() if get('ui.overlay_monitor_policy', 'active_window') == 'cursor'
                        else (foreground.get('center') if saved_monitor is None else None))
                    self.monitor_id = chosen['monitor_id']
                    self.position = placement(chosen, (size, size), positions.get(self.monitor_id))
                chosen = next((m for m in monitors if m['monitor_id'] == self.monitor_id), None)
                if chosen and not dragging:
                    self.position = placement(chosen, (size, size), self.position)
                if drag_monitor and not dragging:
                    positions['_last_monitor'] = self.monitor_id
                    persist()
                    drag_monitor = None
                visible = bool(requested and not fullscreen and monitors)
                if not visible:
                    if pointer.down: pointer.release(window.cursor(), cancel=True)
                    if dragging:
                        mouse('cancel', window.cursor())
                        window.release_capture()
                    window.show(False)
                else:
                    status = self.state.state
                    level = getattr(self.state, 'audio_level', 0)*12 if status in (AgentState.LISTENING, AgentState.USER_SPEAKING, AgentState.RECORDING) else 0
                    if status == AgentState.SPEAKING:
                        level = getattr(self.state, 'tts_level', 0)*6
                    cursor = window.cursor()
                    hovered = hit_orb(cursor[0]-self.position[0], cursor[1]-self.position[1], size)
                    window.present(renderer.render(status, level, dt, mic_visible=hovered), *self.position)
                    if not visible_before:
                        window.show(True)
                visible_before = visible
                self.stop_event.wait(max(.001, (1/fps if visible else .08)-(monotonic()-now)))
        finally:
            if pointer.down: pointer.release(window.cursor(), cancel=True)
            window.close()
            self.hwnd = None
