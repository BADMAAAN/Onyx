"""Continuous capture with independent VAD, endpointing and bounded STT workers.

Whisper is a windowed decoder, not a native streaming model. Partial hypotheses
are provisional; only final hypotheses can reach the orchestrator.
"""
from collections import deque, OrderedDict
from dataclasses import dataclass
from queue import Queue, Empty, Full
from threading import Event, Thread, RLock
from functools import wraps
from time import monotonic
import re
import uuid
import logging
import hashlib
import numpy as np


def unfinished(text):
    text = text.casefold().rstrip(' .,!?:;…')
    if re.search(r'(?:^|\s)(?:его|её|ее|открой|запусти)$', text):
        # A pronoun can complete a window action's object. Reuse the existing
        # verb/argument grammar: maximize/minimize/close are complete here,
        # whereas a move without its destination still has no valid plan.
        from kk.core.desktop_commands import parse_plan
        plan = parse_plan(text)
        if plan and plan[-1].get('kind') in {'window', 'open', 'target'}:
            return False
    return bool(re.search(r'(?:^|\s)(открой|запусти|перенеси|напиши|поставь|сделай|громкость|volume|его|её|ее|на|в|и|нет|хотя|потом|ещё|еще|ну|э+|эм+|подожди|open|the|to|and|actually|no)$', text))


def turn_locked(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return guarded


class VAD:
    def __init__(self, threshold=.0015, mode=2):
        import webrtcvad
        self.detector = webrtcvad.Vad(mode)
        self.threshold = threshold

    def process(self, pcm):
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)/32768
        level = float(np.sqrt(np.mean(samples*samples))) if samples.size else 0
        return level >= self.threshold and self.detector.is_speech(pcm, 16000), level


class TurnManager:
    def __init__(self, silence=.75, incomplete_silence=1.8, unknown_silence=1.2,
                 minimum_speech=.12, maximum=30, pre_roll=.3, partial_interval=1.0):
        self.silence, self.incomplete_silence = silence, incomplete_silence
        self.unknown_silence = unknown_silence
        self.minimum_speech, self.maximum = minimum_speech, maximum
        self.partial_interval = partial_interval
        self.pre_roll = deque(maxlen=max(1, round(pre_roll/.03)))
        self.sequence = 0
        self.lock = RLock()
        self.reset()

    @turn_locked
    def reset(self):
        self.active = False
        self.frames = []
        self.candidates = 0
        self.quiet = 0
        self.partial = ''
        self.last_partial = 0
        self.started = 0
        self.manual = False
        self.hypothesis_samples = 0
        self.speech_samples = 0
        self.pre_roll.clear()

    @turn_locked
    def hypothesis(self, sequence, text, samples=None):
        if sequence == self.sequence and self.active:
            self.partial = text
            self.hypothesis_samples = samples if samples is not None else len(self.frames)*480

    @turn_locked
    def begin_manual(self, now):
        self.reset()
        self.sequence += 1
        self.active = self.manual = True
        self.started = self.last_partial = now

    @turn_locked
    def commit_manual(self):
        if not self.manual:
            return []
        sequence, audio = self.sequence, b''.join(self.frames)
        has_speech = self.speech_samples > 0
        self.reset()
        return [('final_audio', sequence, audio)] if has_speech else [('manual_cancelled', sequence, 'empty')]

    def endpoint_delay(self):
        text = self.partial.casefold().rstrip(' .,!?:;…')
        if not text:
            return self.unknown_silence
        if unfinished(text):
            return self.incomplete_silence
        return self.silence

    @turn_locked
    def feed(self, pcm, speech, now):
        events = []
        if not self.active:
            self.pre_roll.append(pcm)
            self.candidates = self.candidates + 1 if speech else 0
            if self.candidates*.03 < self.minimum_speech:
                return events
            self.sequence += 1
            self.active = True
            self.started = self.last_partial = now
            self.frames = list(self.pre_roll)
            events.append(('speech_start', self.sequence, now))
        else:
            self.frames.append(pcm)
        self.quiet = 0 if speech else self.quiet + .03
        if speech:
            self.speech_samples = len(self.frames)*480
        if now-self.started >= (getattr(self, 'manual_maximum', 300) if self.manual else self.maximum):
            # A truncated utterance cannot safely become an executable command.
            events.append(('discard', self.sequence, 'maximum_utterance_duration'))
            self.reset()
        elif (not self.manual and not unfinished(self.partial)
              and self.hypothesis_samples >= self.speech_samples
              and self.quiet >= self.endpoint_delay()):
            trim = max(0, int((self.quiet-.18)/.03))
            frames = self.frames[:-trim] if trim else self.frames
            events.append(('final_audio', self.sequence, b''.join(frames)))
            self.reset()
        elif now-self.last_partial >= self.partial_interval:
            events.append(('partial_audio', self.sequence, b''.join(self.frames)))
            self.last_partial = now
        return events


from .conversation import ConversationSession as ConversationManager


class StreamingSTT:
    def __init__(self, stt, emit):
        self.stt, self.emit = stt, emit
        self.busy = Event()
        self.finals = Queue(3)
        self.partial = None
        self.lock = RLock()
        self.exit = Event()
        self.epoch = 0
        self.capture_id = uuid.uuid4().hex
        self.final_sequences = OrderedDict()
        self.final_audio = OrderedDict()
        self.worker = Thread(target=self._run, name='KKStreamingSTT', daemon=True)
        self.worker.start()

    def submit(self, kind, sequence, pcm):
        with self.lock:
            audio_id = None
            turn_id = None
            if kind == 'final_audio':
                # Endpoint retries must not turn an identical buffer into a new
                # user request. Hash only, never retain microphone audio here.
                audio_id = hashlib.sha256(pcm).hexdigest()
                if sequence in self.final_sequences or audio_id in self.final_audio:
                    return False
                turn_id = self.capture_id + ':' + str(sequence)
                self.final_sequences[sequence] = turn_id
                self.final_audio[audio_id] = turn_id
                for identities in (self.final_sequences, self.final_audio):
                    while len(identities) > 128:
                        identities.popitem(last=False)
            elif sequence in self.final_sequences:
                return False
            item = (self.epoch, kind, sequence, pcm, monotonic(), turn_id, audio_id)
            if kind == 'partial_audio':
                self.partial = item  # latest only: never accumulate partial work
            else:
                self.partial = None
                try:
                    self.finals.put_nowait(item)
                except Full:
                    self.invalidate()
                    self.emit('error', sequence, 'STT overloaded; repeat the command')
            return True

    def invalidate(self):
        with self.lock:
            self.epoch += 1
            self.partial = None
            while True:
                try: self.finals.get_nowait()
                except Empty: break

    def _run(self):
        while not self.exit.wait(.02):
            with self.lock:
                try: item = self.finals.get_nowait()
                except Empty:
                    item, self.partial = self.partial, None
            if item is None:
                continue
            epoch, kind, sequence, pcm, queued, trace_id, audio_id = item
            self.busy.set()
            try:
                audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)/32768
                if kind == 'final_audio' and hasattr(self.stt, 'transcribe_final'):
                    result = self.stt.transcribe_final(audio, lambda: epoch != self.epoch or self.exit.is_set())
                else:
                    result = self.stt.transcribe(audio)
                if kind == 'final_audio':
                    result.trace_id = result.turn_id = trace_id
                    result.audio_id = audio_id
                    for attempt, row in enumerate(getattr(result, 'passes', []), 1):
                        row.update(turn_id=trace_id, audio_id=audio_id, attempt=attempt)
                if epoch == self.epoch and not self.exit.is_set():
                    self.emit('final' if kind == 'final_audio' else 'partial', sequence,
                              dict(result=result, queued=queued, ready=monotonic(), epoch=epoch,
                                   samples=len(pcm)//2, pcm=pcm if kind == 'final_audio' else None,
                                   trace_id=trace_id, turn_id=trace_id, audio_id=audio_id))
            except Exception as exc:
                logging.getLogger('kk').exception('STT worker failed sequence=%s trace_id=%s', sequence, trace_id)
                if epoch == self.epoch:
                    self.emit('error', sequence, str(exc))
            finally:
                # No raw utterance retained after intent recognition, even while idle.
                audio = pcm = item = None
                self.busy.clear()

    def close(self):
        self.exit.set()
        self.invalidate()
        self.worker.join(3)


class BargeInController:
    """VAD proposes a candidate; accepted non-echo language confirms interruption."""
    def __init__(self, interrupt, performance=None):
        self.interrupt, self.performance = interrupt, performance
        self.started = None
        self.token = None
        self.confirmed = False

    def speech_started(self, token=None):
        self.reject('replaced_candidate')
        self.started = monotonic()
        self.token = token
        self.confirmed = False
        if token and not token.event.is_set():
            token.interruption_pending.set()
            token.mark('barge_in_candidate', source='microphone_vad')

    def confirm(self, text, accepted=True, echo=False, final=False):
        if self.started is None or not accepted or echo or not text.strip():
            if final or echo:
                self.reject('echo' if echo else 'rejected_transcript')
            return False
        words = re.findall(r'\w+', text.casefold())
        explicit_stop = text.casefold().strip(' .!?') in {'стоп', 'замолчи', 'прекрати', 'остановись', 'хватит', 'отмена', 'stop', 'cancel'}
        if not final and len(words) < 2 and not explicit_stop:
            return False
        if not self.confirmed:
            started = monotonic()
            self.interrupt()
            self.confirmed = True
            if self.token:
                self.token.mark('confirmed_barge_in', source='accepted_final' if final else 'accepted_partial')
            if self.performance:
                self.performance.record('barge_in_stop_request', monotonic()-started,
                    basis='accepted non-echo transcript to playback stop; acoustic latency excluded')
        # A confirmed partial pauses new desktop actions until the final intent.
        if final:
            self.reject('final_transcript')
        return True

    def reject(self, reason='rejected_transcript'):
        if self.token:
            self.token.interruption_pending.clear()
            self.token.mark('barge_in_candidate_end', reason=reason)
        self.token = None
        self.started = None

    def expire(self, timeout=4):
        if not self.confirmed and self.started is not None and monotonic()-self.started > timeout:
            self.reject('candidate_timeout')



class AudioCapture:
    def __init__(self, microphone, turn, vad, decoder, emit, on_speech, echo=None, native_wakeword=None):
        self.microphone, self.turn, self.vad = microphone, turn, vad
        self.decoder, self.emit, self.on_speech, self.echo = decoder, emit, on_speech, echo
        self.exit = Event()
        self.ready = Event()
        self.last_error = None
        self.native_wakeword = native_wakeword
        self.echo_gate = None
        self.suppress_input = None
        self.native_buffer = bytearray()
        self.enabled = lambda: True
        self.paused = Event()
        self.controls = Queue()
        self.thread = Thread(target=self._run, name='KKCapture', daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        from .audio_input import AudioInput
        from .audio_devices import find_device
        last_level = 0
        suppressed_before = False
        while not self.exit.is_set():
            try:
                with AudioInput(find_device(self.microphone, input_device=True), 480) as stream:
                    while not self.exit.is_set():
                        while not self.controls.empty():
                            action = self.controls.get_nowait()
                            if action == 'start':
                                self.decoder.invalidate()
                                self.turn.begin_manual(monotonic())
                                self.emit('manual_started', self.turn.sequence, None)
                            elif action == 'commit':
                                for kind, seq, data in self.turn.commit_manual():
                                    if kind == 'final_audio':
                                        self.emit('manual_committing', seq, None)
                                        self.decoder.submit(kind, seq, data)
                                    else:
                                        self.emit(kind, seq, data)
                            elif action == 'cancel':
                                self.turn.reset()
                                self.turn.sequence += 1
                                self.decoder.invalidate()
                                self.emit('manual_cancelled', self.turn.sequence, None)
                        pcm, overflow = stream.read(480)
                        if pcm and not overflow:
                            self.last_error = None
                            self.ready.set()
                        suppressed = (not self.turn.manual and self.suppress_input
                                      and self.suppress_input() is True)
                        if suppressed or suppressed_before:
                            # Error prompts are deliberately half-duplex, through
                            # output drain and the short room tail. Ordinary
                            # answer playback still supports AEC + barge-in.
                            if not self.turn.manual:
                                self.turn.reset()
                            self.native_buffer.clear()
                            if bool(suppressed) != suppressed_before:
                                self.decoder.invalidate()
                            suppressed_before = bool(suppressed)
                            if suppressed:
                                continue
                        if self.paused.is_set() or not self.enabled():
                            self.turn.reset()
                            self.native_buffer.clear()
                            continue
                        if overflow or not pcm:
                            self.turn.reset()
                            self.decoder.invalidate()
                            continue
                        reference_echo = self.echo_gate and self.echo_gate(pcm) is True
                        if self.echo:
                            pcm = self.echo.process(pcm)
                        if reference_echo:
                            # Still feed real mic/ref to AEC so it converges;
                            # only gate the pure playback packet before VAD.
                            pcm = bytes(len(pcm))
                        if self.native_wakeword and not self.turn.manual:
                            self.native_buffer.extend(pcm)
                            while len(self.native_buffer) >= 2560:
                                frame = bytes(self.native_buffer[:2560]); del self.native_buffer[:2560]
                                if self.native_wakeword.process(frame)['activated']:
                                    self.emit('native_wake', self.turn.sequence, None)
                                    self.native_wakeword.reset()
                        speech, level = self.vad.process(pcm)
                        now = monotonic()
                        if now-last_level > .08:
                            self.emit('level', self.turn.sequence, level)
                            last_level = now
                        for kind, sequence, data in self.turn.feed(pcm, speech, now):
                            if kind == 'speech_start':
                                self.on_speech()  # VAD candidate; language/echo checks confirm interruption
                                self.emit(kind, sequence, data)
                            elif kind.endswith('_audio'):
                                self.decoder.submit(kind, sequence, data)
                            else:
                                self.decoder.invalidate()
                                self.emit(kind, sequence, data)
            except Exception as exc:
                logging.getLogger('kk').exception('Audio capture worker failed')
                self.last_error = str(exc)
                self.ready.clear()
                self.turn.reset()
                self.decoder.invalidate()
                self.emit('error', self.turn.sequence, 'Capture: '+str(exc))
                self.exit.wait(1)

    def close(self):
        self.exit.set()
        self.thread.join(4)
