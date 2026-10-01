from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from kk.config import Config
from kk.core.state import StateMachine, AgentState
from kk.core.task import AgentTask
from kk.core.fast_commands import run_fast, normalize
from kk.tools.registry import ToolResult
from kk.ui.orb_model import placement, load_positions, save_positions, is_fullscreen, hit_orb, select_monitor
from kk.ui.orb_renderer import OrbRenderer


MONITOR = dict(monitor_id='left', left=-1920, top=-200, right=0, bottom=880,
               work_area=[-1920, -200, 0, 840])


class OrbTests(unittest.TestCase):
    def test_drag_across_monitors_persists_and_reloads(self):
        from kk.ui.overlay import Overlay
        monitors = [MONITOR, dict(monitor_id='right', left=0, top=0, right=1920,
                                 bottom=1080, work_area=[0, 0, 1920, 1040])]
        class Window:
            def __init__(self, size, mouse, hit):
                self.hwnd, self.mouse, self.step = 42, mouse, 0
            def cursor(self):
                return (-96, 744) if self.step < 3 else (500, 500)
            def pump(self):
                self.step += 1
                event = {2: 'down', 3: 'move', 4: 'up'}.get(self.step)
                if event:
                    self.mouse(event, self.cursor())
                return self.step < 6
            def move(self, *args): pass
            def show(self, *args): pass
            def present(self, *args): pass
            def close(self): pass
            def left_button_down(self): return self.step in (2, 3)
            def release_capture(self): pass
        with TemporaryDirectory() as folder:
            # The historical drag behavior is retained only behind this flag.
            config = Config(Path(folder), {'ui': {'orb_compatibility': True, 'orb_renderer': 'native'}})
            with patch('kk.ui.orb_window.OrbWindow', Window), \
                 patch('kk.ui.orb_window.foreground_info', return_value={'center': (-500, 500)}), \
                 patch('kk.tools.windows.native.list_monitors', return_value=monitors):
                overlay = Overlay(StateMachine(), config)
                overlay._run()
            saved = load_positions(config.root/'data/orb_positions.json')
            self.assertEqual(saved['right'], [428, 428])
            self.assertEqual(saved['_last_monitor'], 'right')
            self.assertEqual(placement(monitors[1], (144, 144), saved['right']), (428, 428))

    def test_native_click_does_not_activate_window(self):
        from kk.ui.orb_window import OrbWindow
        window = object.__new__(OrbWindow)
        self.assertEqual(window._dispatch(0, 0x0021, 0, 0), 3)

    def test_visibility_does_not_pause_voice_state(self):
        state = StateMachine()
        state.set(AgentState.LISTENING, 'private transcript')
        self.assertEqual(state.set_orb_visible(False), {'visibility': 'hidden'})
        self.assertEqual(state.state, AgentState.LISTENING)
        state.set(AgentState.SPEAKING)
        self.assertFalse(state.orb_visible)
        state.set_orb_visible(True)
        self.assertTrue(state.orb_visible)

    def test_visibility_commands_use_registry(self):
        for request, expected in [('Кей Кей, сделай себя невидимым', False),
                                  ('KK, сделай себя видимым', True),
                                  ('Окей-окей, сделай себя невидимым', False)]:
            state = StateMachine()
            calls = []
            def execute(name, args):
                calls.append((name, args))
                return ToolResult(True, state.set_orb_visible(**args))
            task = AgentTask(request)
            answer = run_fast(request, task, SimpleNamespace(execute=execute), state)
            self.assertTrue(answer)
            self.assertEqual(calls, [('set_orb_visibility', {'visible': expected})])
            self.assertEqual(state.orb_visible, expected)

    def test_cancelled_visibility_command_does_not_run(self):
        task = AgentTask('скрой шар')
        task.cancellation_token.cancel()
        with self.assertRaises(InterruptedError):
            run_fast(task.user_request, task, None, StateMachine())

    def test_normalization_keeps_application_names(self):
        self.assertEqual(normalize('Окей-окей, открой PowerShell'), 'открой PowerShell')
        self.assertEqual(normalize('KKTools'), 'KKTools')  # Not an agent alias.

    def test_borderless_fullscreen_on_negative_monitor(self):
        window = dict(hwnd=123, bounds=[-1920, -200, 0, 880])
        self.assertTrue(is_fullscreen(window, MONITOR))
        self.assertFalse(is_fullscreen(dict(window, has_caption=True), MONITOR))
        self.assertFalse(is_fullscreen(dict(window, minimized=True), MONITOR))
        self.assertFalse(is_fullscreen(dict(window, class_name='WorkerW'), MONITOR))

    def test_maximized_work_area_not_fullscreen(self):
        self.assertFalse(is_fullscreen(dict(hwnd=123, bounds=MONITOR['work_area']), MONITOR))
        self.assertFalse(is_fullscreen({}, MONITOR))

    def test_multi_monitor_position_roundtrip(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'orb_positions.json'
            rows = {'left': [-800, -50], 'right': [200, 300], '_last_monitor': 'left'}
            save_positions(path, rows)
            self.assertEqual(load_positions(path), rows)
            self.assertEqual(placement(MONITOR, (144, 144), rows['left']), (-800, -50))
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_invalid_saved_positions_and_disconnect(self):
        for invalid in (None, 'bad', [1], [float('nan'), 2], ['x', 2]):
            self.assertEqual(placement(MONITOR, (144, 144), invalid), (-168, 672))
        self.assertEqual(placement(MONITOR, (144, 144), [3000, 3000]), (-144, 696))
        self.assertEqual(select_monitor([MONITOR], 'unplugged'), MONITOR)
        with TemporaryDirectory() as folder:
            path = Path(folder)/'bad.json'
            for content in ('[]', '{broken', 'null'):
                path.write_text(content)
                self.assertEqual(load_positions(path), {})

    def test_only_disc_is_draggable(self):
        self.assertTrue(hit_orb(72, 72, 144))
        self.assertFalse(hit_orb(0, 0, 144))
        self.assertFalse(hit_orb(72, 0, 144))

    def test_transparent_square_corners_opaque_disc(self):
        image = np.asarray(OrbRenderer(144).render(AgentState.IDLE))
        self.assertEqual(image.shape, (144, 144, 4))
        self.assertEqual(int(image[0, 0, 3]), 0)
        self.assertEqual(int(image[72, 72, 3]), 255)

    def test_animation_moves_and_audio_response_is_smoothed(self):
        renderer = OrbRenderer(80)
        first = np.asarray(renderer.render(AgentState.IDLE))
        for _ in range(30):
            last = np.asarray(renderer.render(AgentState.THINKING))
        self.assertFalse(np.array_equal(first, last))
        renderer.render(AgentState.SPEAKING, 1.)
        self.assertGreater(renderer.level, 0)
        self.assertLess(renderer.level, .5)
        for _ in range(30):
            renderer.render(AgentState.SPEAKING, 1.)
        self.assertGreater(renderer.level, .9)

    def test_no_transcript_surface_in_default_overlay(self):
        from kk.ui.overlay import Overlay
        with TemporaryDirectory() as folder:
            overlay = Overlay(StateMachine(), Config(Path(folder), {}))
            self.assertTrue(overlay.state.orb_visible)
            # Rendering only accepts state/audio; transcripts cannot leak into pixels.
            import inspect
            self.assertNotIn('detail', inspect.signature(OrbRenderer.render).parameters)


if __name__ == '__main__':
    unittest.main()
