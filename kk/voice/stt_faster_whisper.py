from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import tempfile
from threading import Event
from time import monotonic, perf_counter
import wave
import numpy as np

from faster_whisper import WhisperModel
import sounddevice as sd
import webrtcvad

from kk.performance import PerformanceRecorder

from .audio_devices import find_device, list_audio_devices
from .audio_input import AudioInput


@dataclass(slots=True)
class Transcription:
    text: str
    language: str
    language_probability: float
    duration_seconds: float
    latency_seconds: float
    raw_transcript: str = ''
    normalized_transcript: str = ''
    accepted: bool = True
    rejection_reason: str = ''
    words: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    confidence: float = 1.0
    chosen_stt_pass: str = 'fast'
    passes: list = field(default_factory=list)
    clarification: str = ''
    intent_confidence: float | None = None
    committed: bool = False
    capture_epoch: int | None = None
    trace_id: str | None = None
    turn_id: str | None = None
    audio_id: str | None = None


class FasterWhisperSTT:
    SAMPLE_RATE = 16_000
    FRAME_MS = 30
    FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

    def __init__(self, model_name: str = "small", device: str = "cpu", compute_type: str = "int8", language: str = "ru", microphone: str | int | None = None, download_root: Path | None = None, performance: PerformanceRecorder | None = None):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = None if language in ('auto', '', None) else language
        self.microphone = microphone
        self.download_root = str(download_root) if download_root else None
        self.performance = performance
        self._model: WhisperModel | None = None
        self.cancel_event = Event()
        self.cpu_threads = 0
        self.last_capture = {}
        self.hotwords = []
        self.log_prob_threshold = -0.8
        self.no_speech_threshold = 0.6
        self.compression_ratio_threshold = 2.4
        self.hallucination_silence_threshold = 1.0
        self.minimum_word_probability = 0.35
        self.beam_size = 5
        self.second_pass = None
        self.resolver = None
        self.final_options = dict(beam_size=5, vad_filter=False, temperature=0.0,
                                  condition_on_previous_text=False)
        self.retry_options = dict(beam_size=8, vad_filter=True, temperature=0.0,
                                  condition_on_previous_text=False)

    @staticmethod
    def devices():
        return list_audio_devices()

    def cancel(self) -> None:
        self.cancel_event.set()

    def prewarm(self):
        model=self._load()
        if not getattr(self,'_warm',False):
            started=perf_counter()
            segments,_=model.transcribe(np.zeros(self.SAMPLE_RATE,dtype=np.float32),
                language=self.language,vad_filter=False,beam_size=5,condition_on_previous_text=False)
            list(segments)
            if self.performance: self.performance.record('startup_stt_inference',perf_counter()-started,model=self.model_name)
            self._warm=True

    def _load(self) -> WhisperModel:
        if self._model is None:
            started=perf_counter()
            if getattr(self,'before_load',None): self.before_load()
            if self.device == 'cuda':
                # Pip CUDA wheels on Windows do not add their bin dirs to PATH.
                import os, sys
                self._dll_handles = []
                for folder in (Path(sys.prefix)/'Lib'/'site-packages'/'nvidia').glob('*/bin'):
                    os.environ['PATH'] = str(folder) + os.pathsep + os.environ['PATH']
                    if hasattr(os, 'add_dll_directory'):
                        self._dll_handles.append(os.add_dll_directory(str(folder)))
            model_source = self.model_name
            if not Path(model_source).is_dir():
                from faster_whisper.utils import download_model
                from huggingface_hub.errors import LocalEntryNotFoundError
                try:
                    model_source = download_model(self.model_name,cache_dir=self.download_root,local_files_only=True)
                except LocalEntryNotFoundError:
                    pass
            self._model = WhisperModel(
                model_source,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
                download_root=self.download_root,
                local_files_only=True,
            )
            if self.performance: self.performance.record('startup_stt_model_load',perf_counter()-started,model=self.model_name)
        return self._model

    def record_until_silence(self, silence_seconds: float = 0.6, max_seconds: float = 30,
                             wait_for_speech_seconds: float = 15, on_event=None,
                             echo_filter=None, initial_pcm=None,
                             calibration_seconds: float = .8, ready_beep: bool = False,
                             minimum_speech_seconds: float = .06,
                             silence_threshold: float = .0015,
                             pre_roll_seconds: float = .3,
                             post_roll_seconds: float = .18,
                             in_memory: bool = True) -> Path | np.ndarray:
        self.cancel_event.clear()
        microphone = find_device(self.microphone, input_device=True)
        vad = webrtcvad.Vad(2)
        import math
        pre_roll: deque[bytes] = deque(maxlen=max(1,math.ceil(pre_roll_seconds*1000/self.FRAME_MS)))
        frames: list[bytes] = [initial_pcm] if initial_pcm else []
        speech_started = bool(initial_pcm)
        speech_started_at = 0.0
        silence_frames = 0
        level_history: deque[float] = deque(maxlen=25)
        speech_candidates = 0
        energy_threshold = 0.0
        ready_sent = False
        ready_ignore_until = 0.0
        needed_silence = max(1, int(silence_seconds * 1000 / self.FRAME_MS))
        started = monotonic()
        self.last_capture = {'source':'physical_microphone','started_monotonic':started,
                             'device_index':microphone,'configured_device':self.microphone}
        needed_speech=max(2,math.ceil(minimum_speech_seconds*1000/self.FRAME_MS))
        try:
            with AudioInput(microphone,self.FRAME_SAMPLES,self.SAMPLE_RATE) as stream:
                while not self.cancel_event.is_set():
                    data, overflow = stream.read(self.FRAME_SAMPLES)
                    if overflow:
                        continue
                    chunk = bytes(data)
                    samples = np.frombuffer(chunk,dtype=np.int16).astype(np.float32)/32768
                    level = float(np.sqrt(np.mean(samples*samples)))
                    elapsed = monotonic()-started
                    # Focusrite's analogue floor can fool WebRTC VAD.  Learn a
                    # short local floor and require both VAD and useful energy.
                    # This is recalculated per utterance and stores no audio.
                    if not speech_started and elapsed < calibration_seconds:
                        if elapsed >= .2:
                            level_history.append(level)
                        is_speech = False
                    else:
                        if not energy_threshold:
                            baseline = float(np.percentile(level_history or [level],85))
                            energy_threshold = max(silence_threshold, baseline*1.35)
                        if not ready_sent:
                            ready_sent = True
                            if ready_beep:
                                try:
                                    import winsound
                                    winsound.Beep(900,120)
                                    ready_ignore_until = monotonic()+.25
                                    pre_roll.clear()
                                except Exception:
                                    pass
                            if on_event:
                                on_event('ready',{'seconds':elapsed,'energy_threshold':energy_threshold})
                        is_speech = vad.is_speech(chunk, self.SAMPLE_RATE) and level >= energy_threshold
                        if monotonic() < ready_ignore_until:
                            is_speech = False
                            pre_roll.clear()
                    if echo_filter and echo_filter(chunk):
                        is_speech = False
                    if on_event:
                        on_event('level', {'rms': level, 'seconds': monotonic()-started})
                    if not speech_started:
                        pre_roll.append(chunk)
                        speech_candidates = speech_candidates + 1 if is_speech else 0
                        if speech_candidates >= needed_speech:
                            speech_started = True
                            speech_started_at = monotonic()
                            self.last_capture['speech_start_detected_monotonic']=speech_started_at
                            if on_event:
                                on_event('speech_start', {'seconds': speech_started_at-started,
                                    'energy_threshold':energy_threshold})
                            frames.extend(pre_roll)
                        elif monotonic() - started >= wait_for_speech_seconds:
                            raise TimeoutError("Речь не обнаружена")
                    else:
                        frames.append(chunk)
                        silence_frames = 0 if is_speech else silence_frames + 1
                        if silence_frames >= needed_silence or monotonic() - started >= max_seconds:
                            ended=monotonic()
                            self.last_capture.update(vad_decided_monotonic=ended,
                                trailing_silence_seconds=silence_frames*self.FRAME_MS/1000,
                                speech_end_estimate_monotonic=ended-silence_frames*self.FRAME_MS/1000,
                                endpoint_basis='last VAD-positive frame; not human-labelled acoustic end')
                            if on_event:
                                on_event('speech_end', {'seconds':monotonic()-started,
                                    'end_of_speech_detection_seconds':silence_frames*self.FRAME_MS/1000})
                            break
        except sd.PortAudioError as exc:
            raise RuntimeError(f"Ошибка микрофона: {exc}") from exc
        if self.cancel_event.is_set():
            raise InterruptedError("Запись отменена")
        if not frames:
            raise RuntimeError("Аудио не записано")
        # Retain a safety post-roll, omit already-confirmed trailing silence.
        trim=max(0,silence_frames-math.ceil(post_roll_seconds*1000/self.FRAME_MS))
        if trim:
            frames=frames[:-trim]
        if in_memory:
            return np.frombuffer(b''.join(frames),dtype=np.int16).astype(np.float32)/32768
        if self.performance:
            self.performance.record("listening_latency", max(0.0, speech_started_at - started), microphone=str(self.microphone))
            self.performance.record("recording_duration", monotonic() - started, microphone=str(self.microphone))
        folder = Path(tempfile.gettempdir()) / "KK"
        folder.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix="voice_", suffix=".wav", dir=folder, delete=False)
        handle.close()
        path = Path(handle.name)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(b"".join(frames))
        return path

    def transcribe(self, audio_path: str | Path | np.ndarray, **options) -> Transcription:
        started = perf_counter()
        hotwords = list(self.hotwords)
        intents = getattr(self, 'personal_intents', None)
        if intents:
            hotwords += list(intents.entries)
            hotwords += [e['target'] for e in intents.entries.values() if e['kind'] == 'app']
        parameters = dict(
            language=self.language,
            vad_filter=True,
            beam_size=self.beam_size,
            condition_on_previous_text=False,
            task='transcribe', temperature=0.0, word_timestamps=True,
            hotwords=', '.join(dict.fromkeys(hotwords)) or None,
            log_prob_threshold=self.log_prob_threshold,
            no_speech_threshold=self.no_speech_threshold,
            compression_ratio_threshold=self.compression_ratio_threshold,
            hallucination_silence_threshold=self.hallucination_silence_threshold,
        )
        parameters.update(options)
        segments, info = self._load().transcribe(
            audio_path if isinstance(audio_path, np.ndarray) else str(audio_path), **parameters)
        segments = list(segments)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        evidence = [dict(start=s.start, end=s.end, text=s.text,
            avg_logprob=s.avg_logprob, no_speech_prob=s.no_speech_prob,
            compression_ratio=s.compression_ratio) for s in segments]
        words = [dict(word=w.word, start=w.start, end=w.end, probability=w.probability)
                 for s in segments for w in (s.words or [])]
        reasons = []
        if not text:
            reasons.append('no_speech')
        if any(s.avg_logprob < self.log_prob_threshold or
               s.no_speech_prob > self.no_speech_threshold or
               s.compression_ratio > self.compression_ratio_threshold for s in segments):
            reasons.append('uncertain_segment')
        if any(w['probability'] < self.minimum_word_probability for w in words):
            reasons.append('uncertain_word')
        latency = perf_counter() - started
        if self.performance:
            self.performance.record("stt_latency", latency, model=self.model_name, device=self.device)
        from kk.core.fast_commands import normalize
        import math
        confidence = min([w['probability'] for w in words] or [math.exp(s.avg_logprob) for s in segments] or [0.0])
        intent_words = words
        import re
        joined = ''.join(w['word'] for w in words)
        wake = re.match(r'(?i)^\s*(?:оникс|onyx|кей[\s-]+кей|kk)\b[ ,.!?-]*', joined)
        if wake:
            consumed = 0
            intent_words = []
            for word in words:
                consumed += len(word['word'])
                if consumed > wake.end():
                    intent_words.append(word)
        intent_confidence = min([w['probability'] for w in intent_words] or [confidence])
        if wake and intent_words and all(w['probability'] >= self.minimum_word_probability for w in intent_words):
            reasons = [reason for reason in reasons if reason != 'uncertain_word']
        result = Transcription(
            text,
            info.language,
            float(info.language_probability),
            float(getattr(info, "duration", 0.0)),
            latency,
            text, normalize(text), not reasons, ','.join(reasons), words, evidence, confidence,
        )
        result.intent_confidence = intent_confidence
        return result

    def transcribe_final(self, audio, cancelled=lambda: False):
        from .recognition import recognize_final
        result = recognize_final(self, audio, cancelled)
        result.committed = not cancelled()
        return result

    def decode_full(self, audio, retry=False):
        options = dict(self.retry_options if retry else self.final_options)
        if retry:
            # A doubtful name must get an independent acoustic decode, not the
            # same dictionary bias with a wider beam. Keep the first pass and
            # all confidence gates unchanged; reuse the resident model/audio.
            options['hotwords'] = None
        return self.transcribe(audio, **options)

    def listen(self, **record_options) -> Transcription:
        audio = self.record_until_silence(**{**record_options,'in_memory':True})
        self.last_capture['stt_started_monotonic']=monotonic()
        result=self.transcribe_final(audio)
        self.last_capture['stt_ready_monotonic']=monotonic()
        return result
