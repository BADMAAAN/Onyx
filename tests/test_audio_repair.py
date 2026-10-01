"""Hardware-free regressions for the production voice producer/consumer boundary."""
from contextlib import ExitStack
from queue import Queue
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import numpy as np

from kk.core.task import CancellationToken
from kk.voice.speech_queue import SpeechQueue, SentenceBuffer
from kk.voice.streaming import BargeInController


class SimulatedOutput:
    """Exercise the actual production callback; model PortAudio's drain separately."""
    instances = []
    drain = None
    latency = .01
    def __init__(self, callback, finished_callback, **kwargs):
        self.callback, self.finished = callback, finished_callback
        self.abort_event = Event()
        self.copied = []
        self.drained = False
        self.worker = None
        self.instances.append(self)
    def start(self):
        import sounddevice as sd
        def run():
            try:
                while not self.abort_event.is_set():
                    data = np.zeros((160, 1), dtype=np.float32)
                    self.callback(data, 160, SimpleNamespace(), SimpleNamespace(output_underflow=False))
                    self.copied.append(data.copy())
                    sleep(.001)
            except sd.CallbackStop:
                if self.drain is not None:
                    self.drain.wait(2)
                self.drained = not self.abort_event.is_set()
            except sd.CallbackAbort:
                pass
            finally:
                self.finished()
        self.worker = Thread(target=run, daemon=True)
        self.worker.start()
    def abort(self):
        self.abort_event.set()
        self.finished()
    def close(self):
        if self.worker:
            self.worker.join(3)


class AudioRepairTests(TestCase):
    def queue(self, synth=None):
        SimulatedOutput.instances = []
        SimulatedOutput.drain = None
        tts = SimpleNamespace(synthesize=synth or (lambda text: np.ones(481, dtype=np.float32)),
            muted=False, volume=1, output_device=None, sample_rate=16000, performance=None)
        stack = ExitStack()
        stack.enter_context(patch('kk.voice.speech_queue.sd.OutputStream', SimulatedOutput))
        stack.enter_context(patch('kk.voice.speech_queue.find_device', return_value=0))
        self.addCleanup(stack.close)
        queue = SpeechQueue(tts)
        self.addCleanup(queue.close)
        return queue

    def test_all_chunks_including_unpunctuated_tail_drain_before_done(self):
        queue = self.queue()
        token = CancellationToken(diagnostic=True)
        queue.begin(token)
        text = ('Первое предложение содержит достаточно слов для полноценного начала. '
                'Второе предложение должно синтезироваться во время первого. '
                'Последнее контрольное окончание: янтарный маяк')
        buffer = SentenceBuffer(lambda chunk: queue.submit(chunk, token))
        for character in text:
            buffer.feed(character)
        buffer.flush()
        queue.finish(token)
        queue.wait(token, 3)
        enqueued = [event for event in token.trace if event['event'] == 'tts_enqueue']
        completed = [event for event in token.trace if event['event'] == 'audio_playback_complete']
        self.assertEqual(' '.join(event['text'] for event in enqueued), text)
        self.assertTrue(enqueued[-1]['text'].endswith('янтарный маяк'))
        self.assertEqual([e['chunk_id'] for e in completed], [1, 2, 3])
        self.assertEqual(sum(e['completed_frames'] for e in completed), 481*3)
        self.assertTrue(all(stream.drained for stream in SimulatedOutput.instances))
        first_callbacks = [event for event in token.trace if event['event'] == 'audio_first_callback']
        self.assertEqual([event['chunk_id'] for event in first_callbacks], [1, 2, 3])
        for first, complete in zip(first_callbacks, completed):
            self.assertEqual(first['clock'], 'perf_counter')
            self.assertLessEqual(first['at'], first['observed_at'])
            self.assertEqual(first['chunk_id'], complete['chunk_id'])
        self.assertEqual(token.trace[-1]['event'], 'speech_complete')

    def test_temporary_empty_queue_is_not_completion_and_final_drain_is_awaited(self):
        queue = self.queue()
        SimulatedOutput.drain = Event()
        token = queue.begin()
        queue.submit('Первый фрагмент', token)
        deadline = monotonic()+2
        while not SimulatedOutput.instances and monotonic() < deadline:
            sleep(.005)
        sleep(.03)
        self.assertFalse(queue.done.is_set())
        self.assertFalse(SimulatedOutput.instances[0].drained)
        queue.submit('Последний фрагмент', token)
        queue.finish(token)
        self.assertFalse(queue.done.is_set())
        SimulatedOutput.drain.set()
        queue.wait(token, 3)
        self.assertEqual(len(SimulatedOutput.instances), 2)
        self.assertTrue(all(stream.drained for stream in SimulatedOutput.instances))

    def test_cancel_during_synthesis_cannot_revive_old_audio(self):
        entered, release = Event(), Event()
        def synth(text):
            entered.set(); release.wait(2)
            return np.ones(480, dtype=np.float32)
        queue = self.queue(synth)
        token = queue.begin()
        queue.submit('Старый ответ', token)
        self.assertTrue(entered.wait(2))
        queue.cancel(reason='confirmed_barge_in', source='test')
        release.set()
        sleep(.1)
        self.assertEqual(SimulatedOutput.instances, [])
        self.assertEqual(token.cancel_reason, 'confirmed_barge_in')
        self.assertEqual(token.cancel_source, 'test')

    def test_vad_noise_and_echo_do_not_cancel_but_confirmed_language_does(self):
        stop = Mock()
        token = CancellationToken()
        barge = BargeInController(stop)
        barge.speech_started(token)
        self.assertTrue(token.interruption_pending.is_set())
        stop.assert_not_called()
        barge.confirm('Из', echo=True)
        self.assertFalse(token.interruption_pending.is_set())
        stop.assert_not_called()
        barge.speech_started(token)
        barge.confirm('шум', accepted=False, final=True)
        self.assertFalse(token.interruption_pending.is_set())
        stop.assert_not_called()
        barge.speech_started(token)
        self.assertTrue(barge.confirm('Подожди, расскажи подробнее'))
        stop.assert_called_once()
        self.assertTrue(token.interruption_pending.is_set())
        barge.confirm('Подожди, расскажи подробнее', final=True)
        self.assertFalse(token.interruption_pending.is_set())
        stop.assert_called_once()

    def test_candidate_timeout_releases_safe_action_boundary(self):
        token = CancellationToken()
        barge = BargeInController(Mock())
        barge.speech_started(token)
        with patch('kk.voice.streaming.monotonic', return_value=barge.started+5):
            barge.expire()
        self.assertFalse(token.interruption_pending.is_set())
        barge.interrupt.assert_not_called()

    def test_confirmed_partial_keeps_actions_paused_until_final_even_after_timeout(self):
        token = CancellationToken()
        barge = BargeInController(Mock())
        barge.speech_started(token)
        barge.confirm('Подожди, сначала другое приложение')
        with patch('kk.voice.streaming.monotonic', return_value=barge.started+10):
            barge.expire()
        self.assertTrue(token.interruption_pending.is_set())
        barge.confirm('Подожди, сначала другое приложение', final=True)
        self.assertFalse(token.interruption_pending.is_set())

    def test_cancel_while_stream_close_finishes_never_aborts_closed_stream(self):
        queue = self.queue()
        entered, release = Event(), Event()
        class ClosingOutput(SimulatedOutput):
            is_closed = False
            def close(self):
                self.is_closed = True
                entered.set()
                release.wait(2)
                super().close()
            def abort(self):
                if self.is_closed:
                    raise RuntimeError('Cannot abort closed stream')
                super().abort()
        with patch('kk.voice.speech_queue.sd.OutputStream', ClosingOutput):
            token = queue.begin()
            queue.submit('Последний фрагмент', token)
            queue.finish(token)
            self.assertTrue(entered.wait(2))
            try:
                self.assertIsNone(queue.stream)
                queue.cancel(reason='user_stop', source='race_test')
                self.assertTrue(queue.done.is_set())
                self.assertTrue(queue.text_queue.empty())
                self.assertTrue(queue.audio_queue.empty())
            finally:
                release.set()

    def test_spoken_fragments_and_pronunciation_are_echo_but_new_question_is_not(self):
        queue = self.queue()
        queue.playing.set()
        queue.current_text = 'Из чайных листьев. ChatGPT помогает отвечать на вопросы.'
        queue.spoken_text = queue.current_text
        self.assertTrue(queue.matches_recent_text('Из'))
        self.assertTrue(queue.matches_recent_text('Чат Джи Пи Ти'))
        self.assertFalse(queue.matches_recent_text('А как его выращивают?'))

    def test_echo_guard_uses_only_current_chunk_and_short_playback_tail(self):
        queue = self.queue()
        queue.spoken_text = 'Скажите стоп, чтобы остановить ответ. Сейчас расскажу о чае.'
        queue.current_text = 'Сейчас расскажу о чае.'
        queue.playing.set()
        self.assertFalse(queue.matches_recent_text('стоп'))
        self.assertTrue(queue.matches_recent_text('о чае'))
        queue.current_text = 'Скажите стоп, чтобы остановить ответ.'
        self.assertTrue(queue.matches_recent_text('стоп'))
        queue.playing.clear()
        queue.last_playback_end = 10
        with patch('kk.voice.speech_queue.perf_counter', return_value=11):
            self.assertTrue(queue.matches_recent_text('стоп'))
        with patch('kk.voice.speech_queue.perf_counter', return_value=12):
            self.assertFalse(queue.matches_recent_text('стоп'))

    def test_stop_and_enough_cancel_task_before_conversation_farewell(self):
        from kk.voice.loop import VoiceLoop
        from kk.voice.stt_faster_whisper import Transcription
        from kk.core.state import StateMachine
        for text in ('остановись', 'хватит'):
            with self.subTest(text=text):
                speech = Mock()
                speech.playing = Event()
                speech.done = Event()
                speech.matches_recent_text.return_value = False
                class Capture:
                    def __init__(self, microphone, turn, vad, decoder, emit, *args):
                        self.emit = emit
                        self.ready = Event(); self.ready.set()
                        self.paused = Event()
                        self.controls = Queue()
                    def start(self):
                        self.emit('native_wake', 0, None)
                        result = Transcription(text, 'ru', 1, 1, .1, committed=True)
                        self.emit('final', 0, dict(result=result, epoch=0, samples=16000, queued=0, ready=.1))
                    def close(self):
                        pass
                stt = Mock(); stt.cancel_event = Event()
                agent = Mock(); agent.orchestrator.current_task = None
                with patch('kk.voice.loop.SpeechQueue', return_value=speech), \
                     patch('kk.voice.streaming.AudioCapture', Capture):
                    loop = VoiceLoop(agent, stt, Mock(), StateMachine(), Mock())
                    loop.wakeword = True
                    original_stop = loop.emergency_stop
                    def stop():
                        original_stop()
                        loop._exit.set()
                    loop.emergency_stop = Mock(side_effect=stop)
                    loop._streaming_session()
                loop.emergency_stop.assert_called_once()
                loop.policy.trigger_stop.assert_called_once()
                agent.cancel.assert_called_once()
                speech.submit.assert_not_called()

    def test_synthesis_failure_has_traceback_and_raises_original_error(self):
        def synth(text):
            raise ValueError('synthetic failure')
        queue = self.queue(synth)
        token = queue.begin()
        with self.assertLogs('kk', level='ERROR') as logs:
            queue.submit('Текст', token)
            with self.assertRaisesRegex(ValueError, 'synthetic failure'):
                queue.wait(token, 3)
        self.assertIn('Traceback', '\n'.join(logs.output))
        self.assertEqual(token.cancel_reason, 'worker_error')
        self.assertTrue(any(e['event'] == 'worker_error' for e in token.trace))

    def test_diagnostic_text_is_opt_in_and_child_ids_match(self):
        token = CancellationToken()
        token.mark('tts_enqueue', text='private words', characters=13)
        self.assertNotIn('text', token.trace[-1])
        token.diagnostic = True
        token.trace_id = 'task-id'
        child = token.speech_child()
        child.mark('tts_enqueue', text='test words')
        self.assertEqual(child.trace[-1]['text'], 'test words')
        for name in ('session_id', 'turn_id', 'response_id'):
            self.assertEqual(getattr(child, name), getattr(token, name))
        self.assertEqual(child.trace[-1]['task_id'], 'task-id')

    def test_complete_pronoun_window_commands_endpoint_without_waiting_for_capture_limit(self):
        from kk.voice.streaming import TurnManager
        for phrase in ('Разверни его', 'Сверни её', 'Закрой его', 'Восстанови её',
                       'Открой Firefox и разверни его'):
            with self.subTest(phrase=phrase):
                turn = TurnManager()
                for index in range(10):
                    turn.feed(bytes(960), True, index*.03)
                turn.hypothesis(turn.sequence, phrase)
                events = []
                for index in range(50):
                    events += turn.feed(bytes(960), False, .3+index*.03)
                finals = [event for event in events if event[0] == 'final_audio']
                self.assertEqual(len(finals), 1)
                self.assertFalse(turn.active)
                self.assertGreater(len(finals[0][2]), 10*960)

    def test_incomplete_move_destination_and_bare_verb_still_wait_for_continuation(self):
        from kk.voice.streaming import TurnManager
        for phrase in ('Перенеси его', 'Перенеси её', 'Перенеси ее', 'Открой', 'Перенеси его на'):
            with self.subTest(phrase=phrase):
                turn = TurnManager()
                for index in range(10):
                    turn.feed(bytes(960), True, index*.03)
                turn.hypothesis(turn.sequence, phrase)
                events = []
                for index in range(100):
                    events += turn.feed(bytes(960), False, .3+index*.03)
                self.assertFalse(any(event[0] == 'final_audio' for event in events))
                self.assertTrue(turn.active)

    def test_concurrent_stop_calls_close_native_playback_once(self):
        from kk.voice.loop import VoiceLoop
        from kk.core.state import StateMachine, AgentState
        entered, release, second_started = Event(), Event(), Event()
        tts, stt, agent = Mock(), Mock(), Mock()
        def native_stop():
            entered.set()
            release.wait(2)
        tts.stop.side_effect = native_stop
        with patch('kk.voice.loop.SpeechQueue', return_value=Mock()):
            loop = VoiceLoop(agent, stt, tts, StateMachine(), Mock())
        first = Thread(target=loop.stop)
        def second_stop():
            second_started.set()
            loop.stop()
        second = Thread(target=second_stop)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        self.assertTrue(second_started.wait(2))
        try:
            sleep(.03)
            tts.stop.assert_called_once()
        finally:
            release.set()
            first.join(2); second.join(2)
        self.assertFalse(first.is_alive() or second.is_alive())
        loop.stop()
        tts.stop.assert_called_once()
        stt.cancel.assert_called_once()
        agent.cancel.assert_called_once()
        self.assertEqual(loop.state.state, AgentState.STOPPED)
        self.assertTrue(loop._exit.is_set())

    def test_new_turn_and_shutdown_do_not_cancel_a_completed_generation(self):
        from kk.voice.loop import VoiceLoop
        from threading import RLock
        loop = VoiceLoop.__new__(VoiceLoop)
        loop.input_lock = RLock()
        loop.generation = CancellationToken()
        previous = loop.generation
        previous.completed.set()
        previous.mark('voice_answer_end', cancelled=False)
        loop.speech = Mock()
        loop.decoder = None
        loop.commands = Queue(1)
        loop.worker = Mock(); loop.worker.is_alive.return_value = True
        loop.agent = Mock(); loop.agent.orchestrator.current_task = None
        evidence = SimpleNamespace(committed=True, accepted=True, capture_epoch=None, trace_id='next-turn')
        loop.dispatch('Следующий вопрос', evidence)
        self.assertFalse(previous.event.is_set())
        self.assertIsNone(previous.cancel_reason)
        self.assertEqual([event['event'] for event in previous.trace], ['voice_answer_end'])
        loop.generation.completed.set()
        loop.interrupt(reason='shutdown', source='test')
        self.assertFalse(loop.generation.event.is_set())
        loop.agent.cancel.assert_called_once()

    def test_answer_completion_is_set_only_after_playback_drains(self):
        from contextlib import nullcontext
        from kk.voice.loop import VoiceLoop
        from kk.core.state import StateMachine
        queue = self.queue()
        SimulatedOutput.drain = Event()
        loop = VoiceLoop.__new__(VoiceLoop)
        loop.speech = queue
        loop.state = StateMachine()
        token = CancellationToken()
        token.speech_token = token.speech_child()
        queue.begin(token.speech_token)
        def ask(text, on_token, cancellation):
            on_token('Полный ответ с проверяемым окончанием.')
        loop.agent = SimpleNamespace(ask=ask)
        with patch('uiautomation.UIAutomationInitializerInThread', return_value=nullcontext()):
            worker = Thread(target=loop._answer, args=('Вопрос', token), daemon=True)
            worker.start()
            deadline = monotonic()+2
            while not SimulatedOutput.instances and monotonic()<deadline:
                sleep(.005)
            self.assertTrue(SimulatedOutput.instances)
            self.assertFalse(token.completed.is_set())
            SimulatedOutput.drain.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(token.completed.is_set())
        self.assertEqual(token.trace[-1]['event'], 'voice_answer_end')
        self.assertTrue(SimulatedOutput.instances[0].drained)

    def test_new_final_cancels_old_generation_before_queueing_new_response(self):
        from kk.voice.loop import VoiceLoop
        from kk.core.state import StateMachine
        from threading import RLock
        loop = VoiceLoop.__new__(VoiceLoop)
        loop.input_lock = RLock()
        loop.generation = CancellationToken()
        old = loop.generation
        loop.speech = Mock()
        loop.decoder = None
        loop.commands = Queue(1)
        loop.worker = Mock()
        loop.worker.is_alive.return_value = True
        loop.agent = SimpleNamespace(orchestrator=SimpleNamespace(current_task=SimpleNamespace(
            cancellation_token=old, metrics={'accepting_revisions': True})))
        evidence = SimpleNamespace(committed=True, accepted=True, capture_epoch=None, trace_id='new-turn')
        loop.dispatch('Новый вопрос', evidence)
        self.assertTrue(old.event.is_set())
        self.assertEqual(old.cancel_source, 'final_transcript')
        self.assertEqual(loop.commands.get_nowait()[0], 'Новый вопрос')
        self.assertFalse(loop.generation.event.is_set())
