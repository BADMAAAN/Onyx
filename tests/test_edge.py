from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from types import SimpleNamespace

from kk.config import Config
from kk.core.state import AgentState, StateMachine
from kk.ui import capture_guard
from kk.ui.edge_model import EdgeSession, edge_presence, voice_energy
from kk.ui.gpu_bridge import CaptureHandshake, renderer_process_owned


class EdgePolicyTests(TestCase):
    def test_idle_desktop_clean_and_readiness_independent(self):
        self.assertFalse(edge_presence(readiness=True)['edge'])
        self.assertTrue(edge_presence(readiness=False, active=True)['edge'])

    def test_user_hide_survives_activation_speech_and_readiness(self):
        state = StateMachine()
        state.set_visual_visible(False)
        state.activate_visual('tray')
        state.set(AgentState.SPEAKING)
        state.set_readiness(True)
        data = state.visual_snapshot()
        self.assertTrue(data['user_visual_hidden'])
        self.assertFalse(edge_presence(user_visual_hidden=data['user_visual_hidden'],
            readiness=data['readiness'], active=True, explicitly_activated=True)['edge'])
        self.assertEqual(state.state, AgentState.SPEAKING)
        state.set_orb_visible(True)
        self.assertFalse(state.user_visual_hidden)

    def test_fullscreen_requires_explicit_activation_and_visual_permission(self):
        for explicit in (False, True):
            self.assertFalse(edge_presence(active=True, fullscreen_hidden=True,
                user_visual_hidden=True, explicitly_activated=explicit)['edge'])
        self.assertFalse(edge_presence(active=True, fullscreen_hidden=True)['edge'])
        self.assertTrue(edge_presence(active=True, fullscreen_hidden=True,
            explicitly_activated=True)['edge'])
        self.assertFalse(edge_presence(active=True, fullscreen_hidden=True,
            explicitly_activated=True, fullscreen_voice_edge=False)['edge'])

    def test_wave_once_and_accent_does_not_follow_service_focus(self):
        session = EdgeSession()
        packet = dict(state='LISTENING', active=True, activation_serial=1)
        session.update(packet, 1., 'left')
        self.assertEqual(session.wave_serial, 1)
        for state in ('USER_SPEAKING', 'THINKING', 'SPEAKING', 'LISTENING'):
            session.update(dict(packet, state=state), 2., 'right')
        self.assertEqual(session.wave_serial, 1)
        self.assertEqual(session.active_screen, 'left')
        session.update(dict(packet, state='SLEEPING', active=False), 3., 'right')
        self.assertFalse(session.active)
        self.assertFalse(session.explicit)
        session.update(dict(packet, activation_serial=2), 4., 'right')
        self.assertEqual(session.wave_serial, 2)
        self.assertEqual(session.active_screen, 'right')

    def test_activation_survives_packet_between_voice_state_updates(self):
        session = EdgeSession()
        self.assertTrue(session.update(dict(state='SLEEPING', activation_serial=1), 1., 'left'))
        self.assertTrue(session.explicit)
        self.assertTrue(session.update(dict(state='LISTENING', activation_serial=1), 1.1, 'left'))
        self.assertEqual(session.wave_serial, 1)

    def test_errors_signal_once_not_forever(self):
        session = EdgeSession()
        self.assertTrue(session.update({'state': 'ERROR'}, 1., 'left'))
        self.assertFalse(session.update({'state': 'ERROR'}, 2., 'left'))
        self.assertFalse(session.update({'state': 'ERROR'}, 3., 'left'))

    def test_playback_meter_not_microphone_or_music(self):
        self.assertEqual(voice_energy('SPEAKING', microphone=1., playback=0.), 0.)
        self.assertEqual(voice_energy('LISTENING', microphone=1., playback=1.), 0.)
        self.assertEqual(voice_energy('THINKING', microphone=1., playback=1.), 0.)
        self.assertGreater(voice_energy('RECORDING', microphone=.05), 0.)
        self.assertGreater(voice_energy('SPEAKING', playback=.05), 0.)
        self.assertEqual(voice_energy('IDLE', playback=.1), 0.)

    def test_default_renderer_failure_keeps_voice_and_does_not_create_orb(self):
        from kk.ui.overlay import Overlay
        with TemporaryDirectory() as folder:
            overlay = Overlay(StateMachine(), Config(Path(folder), {}))
            overlay.stop_event = Mock()
            with patch('kk.ui.gpu_bridge.run', side_effect=RuntimeError('renderer fault')), \
                    patch('kk.ui.orb_window.OrbWindow') as native, self.assertLogs('kk.ui.overlay', 'ERROR'):
                overlay._run()
            native.assert_not_called()
            overlay.stop_event.wait.assert_called_once_with()


class CaptureExclusionTests(TestCase):
    def test_recycled_hwnd_with_different_pid_is_not_our_window(self):
        owner, actual_pid = object(), [777]
        def get_pid(hwnd, destination):
            destination._obj.value = actual_pid[0]
        user32 = SimpleNamespace(GetWindowThreadProcessId=Mock(side_effect=get_pid))
        capture_guard.register(owner)
        capture_guard.update_windows(owner, [123], 777)
        try:
            with patch('ctypes.WinDLL', return_value=user32):
                self.assertTrue(capture_guard.is_own_window(123))
                actual_pid[0] = 888
                self.assertFalse(capture_guard.is_own_window(123))
                self.assertFalse(capture_guard.is_own_window(456))
        finally:
            capture_guard.unregister(owner)

    def test_hwnd_owner_accepts_real_venv_child_not_only_launcher(self):
        processes = {
            10: SimpleNamespace(pid=10, create_time=lambda: 1., parents=lambda: []),
            11: SimpleNamespace(pid=11, create_time=lambda: 2., parents=lambda: [SimpleNamespace(pid=10)]),
            12: SimpleNamespace(pid=12, create_time=lambda: 2., parents=lambda: [SimpleNamespace(pid=999)])}
        with patch('psutil.Process', side_effect=processes.__getitem__):
            self.assertTrue(renderer_process_owned(10, 10))
            self.assertTrue(renderer_process_owned(11, 10))
            self.assertFalse(renderer_process_owned(12, 10))

    def test_hide_ack_then_capture_then_restore(self):
        actions = []
        def send(packet):
            actions.append(packet['hidden'])
            handshake.acknowledge({'capture_ack': packet['token'], 'ok': True})
        handshake = CaptureHandshake(send)
        with handshake.capture_guard():
            actions.append('pixels')
        self.assertEqual(actions, [True, 'pixels', False])
        self.assertFalse(handshake.pending)

    def test_capture_exception_still_restores(self):
        actions = []
        def send(packet):
            actions.append(packet['hidden'])
            handshake.acknowledge({'capture_ack': packet['token'], 'ok': True})
        handshake = CaptureHandshake(send)
        with self.assertRaisesRegex(ValueError, 'grab failed'):
            with handshake.capture_guard():
                raise ValueError('grab failed')
        self.assertEqual(actions, [True, False])

    def test_failed_composition_cannot_return_contaminated_capture(self):
        actions = []
        def send(packet):
            actions.append(packet['hidden'])
            handshake.acknowledge({'capture_ack': packet['token'], 'ok': not packet['hidden']})
        handshake = CaptureHandshake(send, timeout=.001)
        with self.assertRaisesRegex(RuntimeError, 'capture/exclusion_failed'):
            with handshake.capture_guard():
                self.fail('capture must not run before composition acknowledgement')
        self.assertEqual(actions, [True, False])

    def test_timeout_still_attempts_restore_and_discards_late_ack(self):
        packets = []
        def send(packet):
            packets.append(packet)
            if not packet['hidden']:
                handshake.acknowledge({'capture_ack': packet['token'], 'ok': True})
        handshake = CaptureHandshake(send, timeout=.001)
        with self.assertRaises(RuntimeError):
            with handshake.capture_guard():
                self.fail('timed out')
        handshake.acknowledge({'capture_ack': packets[0]['token'], 'ok': True})
        self.assertEqual([p['hidden'] for p in packets], [True, False])
        self.assertFalse(handshake.pending)

    def test_global_registration_has_no_dependency_when_renderer_absent(self):
        events = []
        class Owner:
            @contextmanager
            def capture_guard(self):
                events.append('hide')
                try: yield
                finally: events.append('restore')
        owner = Owner()
        capture_guard.register(owner)
        try:
            with capture_guard.clean_capture():
                events.append('pixels')
        finally:
            capture_guard.unregister(owner)
        with capture_guard.clean_capture():
            events.append('no_renderer_pixels')
        self.assertEqual(events, ['hide', 'pixels', 'restore', 'no_renderer_pixels'])

    def test_ambient_never_hides_renderer_and_reports_metadata_scope(self):
        sender = Mock()
        handshake = CaptureHandshake(sender)
        capture_guard.register(handshake)
        capture_guard.update_windows(handshake, [123], 456)
        try:
            for _ in range(10):
                with capture_guard.ambient_capture() as permitted:
                    self.assertFalse(permitted)
            sender.assert_not_called()
            diagnostics = capture_guard.diagnostics()
            self.assertEqual(diagnostics['counters'], {'ambient_deferred': 10})
            self.assertEqual(diagnostics['windows'], [{'hwnd': 123, 'pid': 456}])
            self.assertFalse(diagnostics['exclusion_pixel_verified'])
            self.assertEqual(diagnostics['ambient_policy'], 'metadata_only_with_renderer')
        finally:
            capture_guard.unregister(handshake)
        with capture_guard.ambient_capture() as permitted:
            self.assertTrue(permitted)

    def test_ambient_deferral_invalidates_old_pixels_and_diff(self):
        from kk.ambient.manager import AmbientContextManager
        from PIL import Image
        with TemporaryDirectory() as folder:
            manager = AmbientContextManager(Path(folder)/'ambient.db')
            manager.frames['monitor'] = Image.new('L', (12, 8), 30)
            manager.diff_region, manager.diff_observed_at = [0, 0, 12, 8], 100.
            handshake = CaptureHandshake(Mock())
            capture_guard.register(handshake)
            try:
                with patch('kk.ambient.manager.ImageGrab.grab') as grab:
                    manager._screen_sample([dict(monitor_id='monitor', left=0, top=0, right=12, bottom=8)])
                grab.assert_not_called()
                self.assertFalse(manager.frames)
                self.assertIsNone(manager.diff_region)
                self.assertEqual(manager.diff_observed_at, 0.)
                self.assertEqual(manager.screen_samples, 0)
                self.assertEqual(manager.screen_deferred, 1)
                self.assertEqual(manager.screen_errors, 0)
            finally:
                capture_guard.unregister(handshake)
                manager.stop()

    def test_capture_diagnostics_preserve_order_and_are_bounded(self):
        def send(packet):
            handshake.acknowledge({'capture_ack': packet['token'], 'ok': True})
        handshake = CaptureHandshake(send)
        with handshake.capture_guard():
            self.assertEqual(handshake.diagnostics()['counters']['capture_begin'], 1)
        self.assertEqual([row['event'] for row in handshake.diagnostics()['timeline']],
                         ['hide_request', 'hide_ack', 'capture_begin', 'capture_end',
                          'restore_request', 'restore_ack'])
        for _ in range(100): handshake.defer_ambient()
        self.assertEqual(len(handshake.diagnostics()['timeline']), 96)
        self.assertEqual(handshake.diagnostics()['counters']['ambient_deferred'], 100)
