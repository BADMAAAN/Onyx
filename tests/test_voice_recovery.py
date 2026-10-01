"""Isolated replay of recognition failures; no devices, tools or user database."""
from queue import Queue
from threading import Event, RLock, Thread
from time import monotonic, sleep
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np

from kk.core.state import AgentState, StateMachine
from kk.core.task import CancellationToken
from kk.voice.dictation import stop_live
from kk.voice.loop import VoiceLoop
from kk.voice.recognition import RecognitionRecovery
from kk.voice.speech_queue import SpeechQueue
from kk.voice.streaming import AudioCapture, StreamingSTT, TurnManager
from kk.voice.stt_faster_whisper import FasterWhisperSTT, Transcription


def heard(text='неясная реплика', accepted=False):
    return Transcription(text, 'ru', 1, 1, .01, text, text,
        accepted=accepted, confidence=1 if accepted else .2,
        intent_confidence=1 if accepted else .2, committed=True,
        rejection_reason='' if accepted else 'low_confidence')


def wait_for(predicate):
    deadline = monotonic() + 2
    while not predicate() and monotonic() < deadline:
        sleep(.01)
    return predicate()


class DecoderIdentityTests(TestCase):
    def test_duplicate_endpoint_audio_has_one_two_pass_decode_and_stable_ids(self):
        stt = FasterWhisperSTT()
        stt.transcribe = Mock(side_effect=[heard(), heard()])
        emitted = []
        decoder = StreamingSTT(stt, lambda *event: emitted.append(event))
        self.addCleanup(decoder.close)
        pcm = np.arange(16000, dtype=np.int16).tobytes()
        self.assertTrue(decoder.submit('final_audio', 1, pcm))
        self.assertFalse(decoder.submit('final_audio', 1, pcm))
        self.assertFalse(decoder.submit('final_audio', 2, pcm))
        self.assertTrue(wait_for(lambda: len(emitted) == 1))
        decoder.invalidate()
        self.assertFalse(decoder.submit('final_audio', 3, pcm))
        result = emitted[0][2]['result']
        self.assertFalse(result.accepted)
        self.assertEqual(stt.transcribe.call_count, 2)
        self.assertEqual(len(result.passes), 2)
        self.assertEqual({row['turn_id'] for row in result.passes}, {result.turn_id})
        self.assertEqual({row['audio_id'] for row in result.passes}, {result.audio_id})
        self.assertEqual([row['attempt'] for row in result.passes], [1, 2])
        self.assertEqual(result.trace_id, result.turn_id)

    def test_new_recording_with_distinct_audio_can_follow_failure(self):
        emitted = []
        stt = NS(transcribe_final=Mock(side_effect=[heard(), heard('новая реплика', True)]))
        decoder = StreamingSTT(stt, lambda *event: emitted.append(event))
        self.addCleanup(decoder.close)
        decoder.submit('final_audio', 1, bytes(960))
        self.assertTrue(wait_for(lambda: len(emitted) == 1))
        decoder.invalidate()
        decoder.submit('final_audio', 2, bytes([1, 0])*480)
        self.assertTrue(wait_for(lambda: len(emitted) == 2))
        self.assertNotEqual(emitted[0][2]['audio_id'], emitted[1][2]['audio_id'])
        self.assertNotEqual(emitted[0][2]['turn_id'], emitted[1][2]['turn_id'])
        self.assertTrue(emitted[1][2]['result'].accepted)


class RecoveryLoopTests(TestCase):
    def streaming_loop(self):
        speech = Mock()
        speech.playing = Event(); speech.done = Event(); speech.done.set()
        speech.matches_recent_text.side_effect = lambda text: text == 'эхо подсказки'
        speech.begin.side_effect = lambda token=None: token
        class Capture:
            def __init__(self, microphone, turn, vad, decoder, emit, *args):
                self.emit, self.turn, self.decoder = emit, turn, decoder
                self.ready = Event(); self.ready.set()
                self.paused = Event(); self.controls = Queue()
            def start(self):
                self.emit('native_wake', 0, None)
            def close(self):
                pass
            def final(self, result, sequence):
                self.turn.sequence = sequence
                result.turn_id = result.trace_id = 'turn-' + str(sequence)
                result.audio_id = 'audio-' + str(sequence)
                self.emit('final', sequence, dict(result=result, epoch=self.decoder.epoch,
                    samples=16000, queued=0, ready=.1))
        agent = Mock(); agent.orchestrator.current_task = None
        stt = NS(microphone=None, cancel_event=Event())
        patcher = patch('kk.voice.streaming.AudioCapture', Capture)
        patcher.start(); self.addCleanup(patcher.stop)
        with patch('kk.voice.loop.SpeechQueue', return_value=speech):
            loop = VoiceLoop(agent, stt, Mock(), StateMachine(), Mock())
        loop.desktop_live_scope = 'isolated-live'
        loop.dispatch = Mock()
        thread = Thread(target=loop._streaming_session)
        thread.start()
        def close():
            loop._exit.set(); thread.join(3)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)
        self.assertTrue(wait_for(lambda: getattr(loop, 'conversation', None)
                                 and loop.conversation.active))
        return loop, speech, agent

    def test_bad_finals_and_own_echo_cannot_make_an_error_speech_loop(self):
        loop, speech, agent = self.streaming_loop()
        for sequence, result in enumerate([heard(), heard(), heard('эхо подсказки', True), heard()], 1):
            loop.capture.final(result, sequence)
            if sequence == 3:
                self.assertTrue(wait_for(lambda: speech.matches_recent_text.call_count >= sequence))
            else:
                self.assertTrue(wait_for(lambda: getattr(loop, 'last_rejection_token', None)
                    and loop.last_rejection_token.turn_id == 'turn-' + str(sequence)))
        self.assertEqual(speech.submit.call_count, 1)
        self.assertEqual(speech.submit.call_args.args[0], 'Не расслышала. Попробуй ещё раз.')
        self.assertTrue(speech.submit.call_args.args[1].suppress_capture)
        self.assertIsNone(loop.clarification.pending)
        self.assertEqual(loop.voice_notice, 'Не расслышала. Попробуй ещё раз.')
        self.assertEqual(loop.state.state, AgentState.LISTENING)
        loop.dispatch.assert_not_called(); agent.ask.assert_not_called()
        # Re-delivery with the same IDs still cannot create a second playback.
        loop.capture.final(heard(), 4)
        sleep(.06)
        self.assertEqual(speech.submit.call_count, 1)
        loop.capture.final(heard('новый вопрос', True), 5)
        self.assertTrue(wait_for(lambda: loop.dispatch.call_count == 1))
        self.assertEqual(loop.voice_notice, '')
        loop.capture.final(heard(), 6)
        self.assertTrue(wait_for(lambda: speech.submit.call_count == 2))

    def test_empty_bad_decode_stays_visual_and_does_not_rearm_spoken_errors(self):
        loop, speech, agent = self.streaming_loop()
        loop.capture.final(heard(''), 1)
        self.assertTrue(wait_for(lambda: bool(loop.voice_notice)))
        self.assertTrue(loop.conversation.active)
        self.assertEqual(loop.state.state, AgentState.LISTENING)
        loop.capture.final(heard(), 2)
        self.assertTrue(wait_for(lambda: loop.last_rejection_token.turn_id == 'turn-2'))
        speech.submit.assert_not_called()
        loop.dispatch.assert_not_called(); agent.ask.assert_not_called()

    def test_stop_live_cancels_clarification_and_pending_wake_without_text_task(self):
        loop, speech, agent = self.streaming_loop()
        loop.capture.final(heard(), 1)
        self.assertTrue(wait_for(lambda: speech.submit.call_count == 1))
        speech.token = speech.submit.call_args.args[1]
        # The most recent generation can be an unrelated typed request.
        independent = CancellationToken(session_id='other')
        independent.source = 'text'; loop.generation = independent
        loop._trigger.set()
        stop_live(loop)
        speech.cancel.assert_called_with(reason='live_stopped', source='live_control')
        self.assertFalse(independent.event.is_set())
        self.assertFalse(loop._trigger.is_set())
        self.assertFalse(loop.conversation.active)
        self.assertIsNone(loop.clarification.pending)
        self.assertIsNone(loop.desktop_live_scope)
        self.assertEqual(loop.voice_notice, '')
        self.assertEqual(loop.capture.controls.get_nowait(), 'cancel')
        # A late rejected callback with its old epoch may not restart speech.
        before = speech.submit.call_count
        loop._reject_final(heard())
        self.assertEqual(speech.submit.call_count, before)

    def test_identity_claim_survives_error_budget_reset(self):
        guard = RecognitionRecovery()
        result = heard(); result.turn_id='turn'; result.audio_id='audio'
        self.assertTrue(guard.claim(result)); self.assertTrue(guard.reject())
        self.assertFalse(guard.reject()); guard.reset()
        self.assertFalse(guard.claim(result))


class ErrorPlaybackGateTests(TestCase):
    def test_error_cue_blocks_capture_until_drain_and_tail_but_normal_answer_does_not(self):
        queue = SpeechQueue(Mock())
        self.addCleanup(queue.close)
        token = queue.begin(); token.suppress_capture = True
        self.assertTrue(queue.capture_suppressed())
        queue.done.set(); queue.capture_quiet_until = 10.45
        with patch('kk.voice.speech_queue.perf_counter', return_value=10.2):
            self.assertTrue(queue.capture_suppressed())
        with patch('kk.voice.speech_queue.perf_counter', return_value=10.46):
            self.assertFalse(queue.capture_suppressed())
        queue.capture_quiet_until = 0
        queue.begin()
        self.assertFalse(queue.capture_suppressed())

    def test_capture_discards_error_playback_packets_and_reopens_cleanly(self):
        reads = []
        class Input:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, count):
                reads.append(len(reads))
                if len(reads) == 4: capture.exit.set()
                return bytes([len(reads), 0])*480, False
        decoder = Mock(); vad = Mock(); vad.process.return_value=(False, .01)
        turn = TurnManager()
        capture = AudioCapture(None, turn, vad, decoder, Mock(), Mock())
        capture.suppress_input = lambda: len(reads) <= 2
        with patch('kk.voice.audio_input.AudioInput', Input), \
             patch('kk.voice.audio_devices.find_device', return_value=0):
            capture._run()
        self.assertEqual(vad.process.call_count, 2)
        self.assertEqual(decoder.invalidate.call_count, 2)
        decoder.submit.assert_not_called()
        self.assertTrue(capture.ready.is_set())

    def test_pcm_echo_tail_is_filtered_without_suppressing_mixed_user_speech(self):
        rng=np.random.default_rng(31)
        ref=rng.normal(0,.12,8000).astype(np.float32)
        queue=SpeechQueue.__new__(SpeechQueue)
        queue.lock=RLock(); queue.playing=Event(); queue.reference=ref
        queue.reference_started=0; queue.last_playback_end=.5
        echo=ref[-480:]*.4
        with patch('kk.voice.speech_queue.perf_counter', return_value=.6):
            self.assertTrue(queue.is_echo((echo*32768).astype(np.int16).tobytes()))
            mixed=echo+rng.normal(0,.02,480)
            self.assertFalse(queue.is_echo((mixed*32768).astype(np.int16).tobytes()))
        with patch('kk.voice.speech_queue.perf_counter', return_value=1):
            self.assertFalse(queue.is_echo((echo*32768).astype(np.int16).tobytes()))
