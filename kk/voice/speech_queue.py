"""Bounded synthesis/playback pipeline; completion means PortAudio drained PCM."""
from __future__ import annotations

from queue import Queue, Empty, Full
from threading import Thread, Event, RLock
from time import perf_counter
import logging
import re
import numpy as np
import sounddevice as sd

from kk.core.task import CancellationToken
from .audio_devices import find_device

logger = logging.getLogger('kk')


class SentenceBuffer:
    def __init__(self, emit, minimum=48, maximum=220):
        self.emit, self.minimum, self.maximum = emit, minimum, maximum
        self.buffer = ''

    def feed(self, text):
        self.buffer += text
        while len(self.buffer) >= self.minimum:
            endings = [m.end() for m in re.finditer(r'[.!?…](?:\s|$)|\n', self.buffer)]
            end = next((p for p in endings if p >= self.minimum), None)
            if end is None and len(self.buffer) > self.maximum:
                end = self.buffer.rfind(' ', self.minimum, self.maximum)
                if end < 0:
                    end = self.maximum
            if end is None:
                break
            self.emit(self.buffer[:end].strip())
            self.buffer = self.buffer[end:]

    def flush(self):
        if self.buffer.strip():
            self.emit(self.buffer.strip())
        self.buffer = ''


class SpeechQueue:
    def __init__(self, tts, state=None):
        self.tts, self.state = tts, state
        self.text_queue, self.audio_queue = Queue(8), Queue(4)
        self.lock = RLock()
        self.closed = Event()
        self.token = None
        self.done = Event()
        self.error = None
        self.first_audio_seconds = None
        self.audio_started = Event()
        self.playing = Event()
        self.reference = None
        self.reference_started = 0
        self.spoken_text = ''
        self.current_text = ''
        self.last_playback_end = 0
        self.capture_quiet_until = 0
        self.stream = None
        self.chunk_sequence = 0
        self.workers = [Thread(target=self._synthesize, name='KKTTS', daemon=True),
                        Thread(target=self._play, name='KKAudio', daemon=True)]
        for worker in self.workers:
            worker.start()

    def begin(self, token=None):
        self.cancel(reason='superseded', source='speech_begin')
        with self.lock:
            self.token = token or CancellationToken()
            self.done = Event()
            self.error = None
            self.first_audio_seconds = None
            self.audio_started.clear()
            self.started = perf_counter()
            self.spoken_text = ''
            self.current_text = ''
            self.chunk_sequence = 0
            return self.token

    def _put(self, queue, item, token):
        while not self.closed.is_set():
            token.check()
            try:
                queue.put(item, timeout=.05)
                return
            except Full:
                pass
        raise InterruptedError('Speech queue closed')

    def submit(self, text, token):
        if text:
            from .prosody import prepare
            if not re.search(r'[\w]', prepare(text), flags=re.UNICODE):
                token.mark('tts_non_speech_skipped', characters=len(text))
                return
            with self.lock:
                if token is not self.token:
                    raise InterruptedError('Obsolete speech response')
                self.chunk_sequence += 1
                chunk_id = self.chunk_sequence
            token.mark('tts_enqueue', chunk_id=chunk_id, characters=len(text), text=text)
            self._put(self.text_queue, (token, chunk_id, text), token)

    def finish(self, token):
        self._put(self.text_queue, (token, None, None), token)

    def wait(self, token, timeout=180):
        started = perf_counter()
        with self.lock:
            done = self.done
        while not done.wait(.05):
            token.check()
            if perf_counter() - started > timeout:
                self.cancel(reason='timeout', source='speech_wait')
                raise TimeoutError('Speech playback timeout')
        if self.error:
            raise self.error
        token.check()

    def _worker_error(self, token, exc, source):
        logger.exception('%s failed task_id=%s response_id=%s', source,
                         getattr(token, 'trace_id', token.generation_id), token.response_id)
        token.mark('worker_error', source=source, error_type=type(exc).__name__)
        with self.lock:
            if token is self.token:
                self.error = exc
                self.cancel(reason='worker_error', source=source)

    def _synthesize(self):
        while not self.closed.is_set():
            try:
                token, chunk_id, text = self.text_queue.get(timeout=.1)
            except Empty:
                continue
            try:
                token.check()
                if text is None:
                    self._put(self.audio_queue, (token, chunk_id, text, None), token)
                    continue
                token.mark('tts_synthesis_start', chunk_id=chunk_id)
                prepare_text = (getattr(self.tts, 'prepare_text', None)
                                if callable(getattr(type(self.tts), 'prepare_text', None)) else None)
                speech_text = prepare_text(text) if callable(prepare_text) else text
                token.mark('speech_ready', chunk_id=chunk_id, speech_text=speech_text,
                           characters=len(speech_text), sample_rate=self.tts.sample_rate)
                synthesize = getattr(self.tts, 'synthesize_prepared', None) if callable(prepare_text) else None
                audio = (None if self.tts.muted or getattr(token, 'silent', False) else
                         (synthesize(speech_text) if callable(synthesize) else self.tts.synthesize(text))*self.tts.volume)
                if audio is not None:
                    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
                    if not len(audio) or not np.isfinite(audio).all():
                        raise ValueError('TTS returned empty or non-finite audio')
                token.mark('tts_synthesis_end', chunk_id=chunk_id,
                           generated_frames=0 if audio is None else len(audio),
                           sample_rate=self.tts.sample_rate,
                           duration_seconds=0 if audio is None else len(audio)/self.tts.sample_rate)
                token.check()  # Silero inference itself cannot be interrupted.
                self._put(self.audio_queue, (token, chunk_id, text, audio), token)
            except InterruptedError:
                pass
            except Exception as exc:
                self._worker_error(token, exc, 'tts_worker')

    def _play_audio(self, token, chunk_id, text, audio):
        finished = Event()
        position = 0
        underflows = 0
        callback_errors = []
        first_callback = []
        first_reported = False
        last_meter = 0.
        rate = self.tts.sample_rate

        def report_first_callback():
            nonlocal first_reported, last_meter
            now = perf_counter()
            if self.state and token is self.token and now-last_meter >= .05:
                last_meter = now
                window = audio[max(0, position-int(rate*.05)):position]
                self.state.tts_level = float(np.sqrt(np.mean(window*window))) if len(window) else 0.
            if first_callback and not first_reported:
                first_reported = True
                token.mark('audio_first_callback', at=first_callback[0], chunk_id=chunk_id,
                           clock='perf_counter', observed_at=perf_counter(),
                           basis='PortAudio callback timestamp; acoustic delay excluded')
                with self.lock:
                    if token is self.token and not self.audio_started.is_set():
                        self.first_audio_seconds = first_callback[0]-self.started
                        self.audio_started.set()
                        if self.tts.performance:
                            self.tts.performance.record('time_to_first_audio', self.first_audio_seconds,
                                trace_id=getattr(token, 'trace_id', token.generation_id),
                                basis='first PortAudio callback; acoustic delay excluded')

        def callback(outdata, frames, timing, status):
            nonlocal position, underflows
            try:
                outdata.fill(0)
                if token.event.is_set():
                    raise sd.CallbackAbort
                if status.output_underflow:
                    underflows += 1
                if position >= len(audio):
                    raise sd.CallbackStop  # pending output drains before finished_callback
                if not first_callback:
                    first_callback.append(perf_counter())
                count = min(frames, len(audio)-position)
                outdata[:count, 0] = audio[position:position+count]
                position += count
            except (sd.CallbackStop, sd.CallbackAbort):
                raise
            except BaseException as exc:
                callback_errors.append(exc)
                raise sd.CallbackAbort

        stream = sd.OutputStream(samplerate=rate, channels=1, dtype='float32',
            device=find_device(self.tts.output_device, input_device=False),
            callback=callback, finished_callback=finished.set)
        start = perf_counter()
        try:
            with self.lock:
                token.check()
                if token is not self.token:
                    raise InterruptedError('Obsolete speech response')
                self.stream = stream
                self.reference = audio[::max(1, rate//16000)]
                # Include device output latency in the render timeline, not just synthesis time.
                self.reference_started = start + float(stream.latency)
                self.playing.set()
                self.current_text = text
                self.spoken_text += ' ' + text
                token.mark('audio_device_start', chunk_id=chunk_id, sample_rate=rate)
                stream.start()
                if self.state:
                    from kk.core.state import AgentState
                    self.state.set(AgentState.SPEAKING, text[:90])
            deadline = start + len(audio)/rate + 10
            while not finished.wait(.01):
                token.check()
                if callback_errors:
                    raise callback_errors[0]
                if perf_counter() > deadline:
                    raise TimeoutError('Audio stream did not complete')
                report_first_callback()
            if callback_errors:
                raise callback_errors[0]
            token.check()
            if position != len(audio):
                raise RuntimeError('Audio stream stopped before all frames were submitted')
            report_first_callback()
            token.mark('audio_playback_complete', chunk_id=chunk_id,
                       completed_frames=position, submitted_frames=position, sample_rate=rate,
                       underflows=underflows, playback_seconds=perf_counter()-start,
                       completion_basis='PortAudio finished_callback after output drain')
        finally:
            report_first_callback()
            token.mark('audio_submitted', chunk_id=chunk_id, submitted_frames=position,
                       sample_rate=rate, underflows=underflows,
                       basis='PCM copied by PortAudio callback; not acoustic measurement')
            # Detach atomically before close: cancel must never abort a closed stream.
            with self.lock:
                if self.stream is stream:
                    self.stream = None
                if token is self.token:
                    self.playing.clear()
                    if getattr(self, 'state', None): self.state.tts_level = 0
                    self.last_playback_end = perf_counter()
                    if getattr(token, 'suppress_capture', False):
                        self.capture_quiet_until = self.last_playback_end + .45
            stream.close()

    def _play(self):
        while not self.closed.is_set():
            try:
                token, chunk_id, text, audio = self.audio_queue.get(timeout=.1)
            except Empty:
                continue
            try:
                token.check()
                with self.lock:
                    if token is not self.token:
                        continue
                    if text is None:
                        token.mark('speech_complete')
                        self.done.set()
                        continue
                if audio is not None:
                    self._play_audio(token, chunk_id, text, audio)
            except InterruptedError:
                pass
            except Exception as exc:
                self._worker_error(token, exc, 'playback_worker')

    def matches_recent_text(self, text):
        """Secondary guard for residual echo that survived PCM AEC/correlation."""
        from .prosody import prepare
        def words(value):
            return re.findall(r'\w+', prepare(value).casefold().replace('ё', 'е'))
        with self.lock:
            if not self.playing.is_set() and perf_counter()-self.last_playback_end > 1.5:
                return False
            # Report history must not suppress a later user repeating an old word.
            spoken = words(self.current_text)
        heard = words(text)
        if not heard:
            return False
        # Include single initial syllables/words: reported false starts were "Из"/"Ну".
        return any(spoken[i:i+len(heard)] == heard for i in range(len(spoken)-len(heard)+1))

    def capture_suppressed(self):
        """Keep recognition-error speech out of the automatic microphone path."""
        with self.lock:
            return bool((self.token and getattr(self.token, 'suppress_capture', False)
                         and not self.done.is_set())
                        or perf_counter() < self.capture_quiet_until)

    def is_echo(self, pcm):
        """Conservative correlation gate; mixed near-end speech remains eligible."""
        with self.lock:
            if (self.reference is None or (not self.playing.is_set()
                    and perf_counter()-self.last_playback_end > .45)):
                return False
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)/32768
            end = min(len(self.reference), max(0, int((perf_counter()-self.reference_started)*16000)))
            ref = self.reference[max(0, end-8000):end]
        if len(ref) < len(samples):
            return False
        energy = np.sqrt(np.convolve(ref*ref, np.ones(len(samples)), mode='valid'))
        correlation = np.correlate(ref, samples, mode='valid')/(energy*np.linalg.norm(samples)+1e-9)
        best = int(np.argmax(np.abs(correlation)))
        if abs(correlation[best]) <= .90:
            return False
        window = ref[best:best+len(samples)]
        gain = float(np.dot(window, samples)/(np.dot(window, window)+1e-9))
        residual = samples-gain*window
        return bool(np.sqrt(np.mean(residual*residual)) < .006)

    def render_reference(self, count):
        with self.lock:
            output = np.zeros(count, dtype=np.float32)
            if self.reference is None:
                return output
            end = int((perf_counter()-self.reference_started)*16000)
            start = end-count
            left, right = max(0, start), min(end, len(self.reference))
            if right > left:
                output[left-start:right-start] = self.reference[left:right]
            return output

    def cancel(self, reason='user_stop', source='speech_queue'):
        with self.lock:
            if self.token and not self.done.is_set():
                self.token.cancel(reason=reason, source=source)
            stream = getattr(self, 'stream', None)
            if stream is not None:
                try:
                    stream.abort()
                except Exception:
                    # A disconnected endpoint must not prevent cancellation/queue drain.
                    logger.exception('Playback abort failed during cancellation')
            self.playing.clear()
            if getattr(self, 'state', None): self.state.tts_level = 0
            self.reference = None
            for queue in (self.text_queue, self.audio_queue):
                while True:
                    try:
                        queue.get_nowait()
                    except Empty:
                        break
            self.done.set()

    def close(self):
        self.cancel(reason='shutdown', source='speech_close')
        self.closed.set()
        for worker in self.workers:
            worker.join(timeout=3)
