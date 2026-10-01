"""Hardware-free regressions for IPC completion and cancellation boundaries."""
from contextlib import nullcontext
from copy import deepcopy
from threading import Event, RLock, Thread
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from kk.core.control import RuntimeControl
from kk.core.state import AgentState, StateMachine
from kk.core.task import AgentTask, CancellationToken, TaskStatus
from kk.providers.resources import ModelResourceManager, PressureGuard, ResourcePressure
from kk.voice.loop import VoiceLoop
from kk.voice.speech_queue import SpeechQueue


class HandlerOnlyControl(RuntimeControl):
    """Capture the production handler without binding a socket or writing IPC files."""
    def __init__(self, root, handler, build=None):
        self.handler = handler


class RuntimeRaceReviewTests(TestCase):
    def control(self, loop):
        runtime = SimpleNamespace(agent=loop.agent, state=StateMachine(), policy=Mock())
        control = HandlerOnlyControl.background(SimpleNamespace(root='unused'), runtime,
                                               SimpleNamespace(loop=loop), Mock())
        return control.handler, runtime

    def token(self, diagnostic=False):
        token = CancellationToken(diagnostic=diagnostic)
        token.trace_id = token.generation_id
        token.speech_token = token.speech_child()
        return token

    def test_completed_ipc_result_cannot_observe_a_later_task_or_mutated_metrics(self):
        loop = VoiceLoop.__new__(VoiceLoop)
        token = self.token(diagnostic=True)
        token.answer = 'first answer'
        task = AgentTask('first', cancellation_token=token, status=TaskStatus.COMPLETED)
        task.metrics = {'steps': {'count': 1}}
        task.actions = [{'tool': 'read', 'verified': True}]
        loop.agent = SimpleNamespace(orchestrator=SimpleNamespace(last_task=task, timeout_seconds=1))
        published = []
        real_set = token.completed.set
        def completed():
            published.append(deepcopy(token.result_snapshot))
            real_set()
        token.completed.set = completed
        loop._complete_turn(token)
        self.assertEqual(published[0]['status'], 'COMPLETED')
        self.assertIn('voice_answer_end', [row['event'] for row in published[0]['trace']])
        task.metrics['steps']['count'] = 99
        task.actions[0]['verified'] = False
        loop.agent.orchestrator.last_task = AgentTask('second', status=TaskStatus.FAILED)
        loop.capture = SimpleNamespace(ready=Event())
        loop.capture.ready.set()
        loop.dispatch_text = Mock(return_value=token)
        handle, _ = self.control(loop)
        result = handle({'operation': 'ask', 'text': 'first', 'diagnostic': True})
        self.assertEqual((result['task_id'], result['answer'], result['status']),
                         (token.trace_id, 'first answer', 'COMPLETED'))
        self.assertEqual(result['metrics'], {'steps': {'count': 1}})
        self.assertTrue(result['actions'][0]['verified'])
        result['actions'][0]['verified'] = False
        self.assertTrue(token.result_snapshot['actions'][0]['verified'])

    def test_cancelled_pending_turn_never_inherits_another_turn_details(self):
        loop = VoiceLoop.__new__(VoiceLoop)
        token = self.token(diagnostic=True)
        token.cancel(reason='superseded', source='test')
        unrelated = AgentTask('different', status=TaskStatus.COMPLETED,
                             metrics={'secret': 'other turn'}, actions=[{'tool': 'other'}])
        loop.agent = SimpleNamespace(orchestrator=SimpleNamespace(last_task=unrelated))
        loop._complete_turn(token)
        self.assertTrue(token.completed.is_set())
        self.assertEqual(token.result_snapshot['status'], 'CANCELLED')
        self.assertEqual(token.result_snapshot['metrics'], {})
        self.assertEqual(token.result_snapshot['actions'], [])
        before = deepcopy(token.result_snapshot)
        loop._complete_turn(token)
        self.assertEqual(token.result_snapshot, before)

    def test_microphone_pause_checks_manual_recording_inside_input_lock(self):
        lock, attempted = RLock(), Event()
        class ProbedLock:
            def __enter__(self):
                attempted.set()
                lock.acquire()
            def __exit__(self, *args):
                lock.release()
        loop = SimpleNamespace(input_lock=ProbedLock(), manual_recording=False,
            manual_pending=False, paused=False, agent=SimpleNamespace(), interrupt=Mock(),
            capture=SimpleNamespace(paused=Event()), audio_focus=Mock())
        handle, runtime = self.control(loop)
        errors = []
        def pause():
            try:
                handle({'operation': 'microphone', 'paused': True})
            except Exception as exc:
                errors.append(exc)
        with lock:
            worker = Thread(target=pause)
            worker.start()
            entered = attempted.wait(1)
            # Manual start commits its state while owning the same input lock.
            loop.manual_recording = True
        worker.join(2)
        self.assertTrue(entered)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertFalse(loop.paused)
        self.assertFalse(loop.capture.paused.is_set())
        loop.interrupt.assert_not_called()
        self.assertEqual(runtime.state.state, AgentState.IDLE)
        loop.manual_recording = False
        self.assertEqual(handle({'operation': 'microphone', 'paused': True}), {'paused': True})
        self.assertEqual(runtime.state.state, AgentState.PAUSED)
        self.assertTrue(loop.capture.paused.is_set())

    def test_silent_text_failure_does_not_synthesize_or_open_audio(self):
        tts = SimpleNamespace(muted=False, volume=1, sample_rate=16000,
                              synthesize=Mock(side_effect=AssertionError('unexpected speech')))
        loop = VoiceLoop.__new__(VoiceLoop)
        loop.speech = SpeechQueue(tts)
        self.addCleanup(loop.speech.close)
        loop.state, loop.paused, loop.audio_focus = StateMachine(), False, None
        loop.agent = SimpleNamespace(ask=Mock(side_effect=ValueError('fixture backend failure')))
        token = self.token(diagnostic=True)
        token.speech_token.silent = True
        loop.speech.begin(token.speech_token)
        with patch('uiautomation.UIAutomationInitializerInThread', return_value=nullcontext()), \
             patch('kk.voice.speech_queue.sd.OutputStream') as output, \
             self.assertLogs('kk', level='ERROR'):
            loop._answer('silent text', token)
        self.assertTrue(token.completed.is_set())
        self.assertEqual(token.result_snapshot['status'], 'FAILED')
        self.assertTrue(token.failure_speech_token.silent)
        self.assertIn('speech_complete', [row['event'] for row in token.failure_speech_token.trace])
        tts.synthesize.assert_not_called()
        output.assert_not_called()

    def test_text_completion_preserves_paused_idle_and_active_conversation_states(self):
        for paused, active, expected in ((True, True, AgentState.PAUSED),
                                        (False, False, AgentState.IDLE),
                                        (False, True, AgentState.LISTENING)):
            with self.subTest(paused=paused, active=active):
                loop = VoiceLoop.__new__(VoiceLoop)
                loop.state, loop.paused = StateMachine(), paused
                loop.conversation = SimpleNamespace(active=active)
                loop.agent = SimpleNamespace(ask=Mock(return_value='answer'))
                loop.speech = Mock(error=None)
                token = self.token()
                with patch('uiautomation.UIAutomationInitializerInThread', return_value=nullcontext()):
                    loop._answer('request', token)
                self.assertEqual(loop.state.state, expected)
                self.assertTrue(token.completed.is_set())


class ResourceCancellationReviewTests(TestCase):
    def manager(self):
        manager = ModelResourceManager(SimpleNamespace(base_url='http://fixture', context_window=4096))
        manager.snapshot = Mock(return_value={'models': []})
        manager.preflight = Mock(return_value={'models': []})
        manager.ensure_unloaded = Mock()
        return manager

    def assert_lock_released(self, manager):
        acquired = []
        def probe():
            acquired.append(manager.lock.acquire(blocking=False))
            if acquired[-1]: manager.lock.release()
        worker = Thread(target=probe)
        worker.start(); worker.join(1)
        self.assertEqual(acquired, [True])

    def test_cancel_during_slow_preflight_stages_never_enters_model_body(self):
        for stage in ('snapshot', 'unload', 'preflight'):
            with self.subTest(stage=stage):
                manager, token, entered = self.manager(), CancellationToken(), []
                def cancel_and_return(state=None):
                    token.cancel(reason='user_stop', source='fixture')
                    return state or {'models': []}
                if stage == 'snapshot':
                    manager.snapshot.side_effect = cancel_and_return
                elif stage == 'unload':
                    manager.snapshot.return_value = {'models': [{'name': 'old'}]}
                    manager.ensure_unloaded.side_effect = lambda name: cancel_and_return()
                else:
                    manager.preflight.side_effect = lambda model: cancel_and_return()
                with patch('kk.providers.resources.process_model_lock', return_value=nullcontext()), \
                     patch('kk.providers.resources.PressureGuard') as guard:
                    with self.assertRaises(InterruptedError):
                        with manager.lease('new', cancellation=token):
                            entered.append(True)
                self.assertEqual(entered, [])
                guard.assert_not_called()
                if stage != 'preflight': manager.preflight.assert_not_called()
                self.assert_lock_released(manager)

    def guard(self, manager):
        with patch('kk.providers.resources.psutil.swap_memory', return_value=SimpleNamespace(used=0)), \
             patch('kk.providers.resources.psutil.disk_io_counters', return_value=None):
            guard = PressureGuard(manager, 'new')
        guard.thread = Mock()
        return guard

    def test_pressure_simplification_persists_through_unload_then_resets(self):
        manager = self.manager()
        events = []
        manager.on_pressure = lambda value: events.append(('pressure', value))
        manager.ensure_unloaded.side_effect = lambda name: events.append(('unload', name))
        guard = self.guard(manager)
        with patch('kk.providers.resources.process_model_lock', return_value=nullcontext()), \
             patch('kk.providers.resources.PressureGuard', return_value=guard), \
             self.assertLogs('kk', level='WARNING'):
            with self.assertRaises(ResourcePressure):
                with manager.lease('new'):
                    guard.fail('fixture pressure')
        self.assertEqual(events, [('pressure', True), ('unload', 'new'), ('pressure', False)])
        self.assert_lock_released(manager)

    def test_near_ram_or_vram_reserve_simplifies_without_changing_reserves(self):
        gb = 1024**3
        for near in ('ram', 'vram'):
            with self.subTest(near=near):
                manager = self.manager()
                manager.on_pressure = Mock()
                reserves = manager.ram_reserve, manager.vram_reserve
                manager.snapshot.return_value = {'vram_total': 16*gb,
                    'vram_free': manager.vram_reserve + (gb//4 if near == 'vram' else 2*gb)}
                guard = self.guard(manager)
                guard.done.wait = Mock(side_effect=[False, True])
                ram = SimpleNamespace(available=manager.ram_reserve + (gb//2 if near == 'ram' else 4*gb), percent=50)
                with patch('kk.providers.resources.psutil.virtual_memory', return_value=ram), \
                     patch('kk.providers.resources.psutil.swap_memory', return_value=SimpleNamespace(used=0)), \
                     patch('kk.providers.resources.psutil.disk_io_counters', return_value=None), \
                     patch('kk.providers.resources.responsiveness', return_value={'responsive': True}), \
                     patch('kk.providers.resources.request_json', return_value={'models': []}):
                    guard._watch()
                manager.on_pressure.assert_called_with(True)
                self.assertFalse(guard.aborted.is_set())
                self.assertEqual(reserves, (manager.ram_reserve, manager.vram_reserve))
