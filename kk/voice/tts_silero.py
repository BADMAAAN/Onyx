from __future__ import annotations

from pathlib import Path
import re
from threading import Event, Lock
from time import perf_counter

import numpy as np
import sounddevice as sd
import soundfile as sf
import torch

from kk.performance import PerformanceRecorder

from .audio_devices import find_device


class SileroTTS:
    def __init__(self, model_path: Path, model_url: str, voice: str = "xenia", sample_rate: int = 48_000, output_device: str | int | None = None, volume: float = 1.0, muted: bool = False, performance: PerformanceRecorder | None = None):
        self.model_path = model_path
        self.model_url = model_url
        self.voice = voice
        self.sample_rate = sample_rate
        self.output_device = output_device
        self.volume = max(0.0, min(float(volume), 1.0))
        self.muted = muted
        self.performance = performance
        self._model = None
        self._stop = Event()
        self._lock = Lock()
        self.cpu_threads = 2
        self._warm = False
        self.fallback_voice = 'baya'
        self.actual_voice = voice
        self.rate = self.pitch = 'medium'
        self._loaded_package = None
        self.english = None

    def runtime_diagnostics(self):
        """Identity captured at load time, never inferred from a configured URL."""
        return {'loaded':self._model is not None, 'configured_url':self.model_url,
                'configured_path':str(self.model_path), 'package':self._loaded_package,
                'voice':self.voice, 'actual_voice':self.actual_voice,
                'sample_rate':self.sample_rate, 'rate':self.rate, 'pitch':self.pitch,
                'accentizer':'silero_package_default', 'external_accentizer':None,
                'english': ({'engine':'Windows SAPI local', 'voice':self.english.actual_voice,
                    'native_rate':self.english.native_rate, 'output_rate':self.sample_rate}
                    if self.english else None)}

    def prewarm(self):
        # Silero/Torch can compile on both the first and second invocation.
        # Run real synthesis before readiness; model load alone is insufficient.
        with self._lock:
            if not self._warm:
                for text in ('Готов.','Привет. Я слушаю.','Блокнот открыт.'):
                    self.synthesize(text)
                if self.english:
                    self.english.synthesize('Hello. I am listening.')
                self._warm = True

    def ensure_model(self) -> Path:
        if not self.model_path.exists():
            raise FileNotFoundError('Local TTS package is missing. Install the voice explicitly before starting voice mode: '+str(self.model_path))
        return self.model_path

    def _load(self):
        if self._model is None:
            started=perf_counter()
            if getattr(self,'before_load',None): self.before_load()
            torch.set_num_threads(self.cpu_threads)
            path = self.ensure_model()
            from hashlib import sha256
            from datetime import datetime, timezone
            digest = sha256()
            with path.open('rb') as package:
                for block in iter(lambda:package.read(1024*1024), b''): digest.update(block)
            self._model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
            self._model.to(torch.device("cpu"))
            self._loaded_package = {'path':str(path.resolve()), 'sha256':digest.hexdigest(),
                'bytes':path.stat().st_size, 'loaded_at':datetime.now(timezone.utc).isoformat(),
                'speakers':list(getattr(self._model, 'speakers', [])),
                'sample_rates':list(getattr(self._model, 'sample_rates', []))}
            if self.performance: self.performance.record('startup_tts_model_load',perf_counter()-started)
        return self._model

    def synthesize(self, text: str) -> np.ndarray:
        return self.synthesize_prepared(self.prepare_text(text))

    def prepare_text(self, text):
        from .tts_sapi import english_sentence
        if self.english and english_sentence(text):
            text = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', text)
            return re.sub(r'[`*_#]', '', text).strip()
        from .prosody import prepare
        pronunciation = self.pronunciation() if callable(getattr(self, 'pronunciation', None)) else None
        return prepare(text, pronunciation)

    def synthesize_prepared(self, text: str) -> np.ndarray:
        from .prosody import ramp
        from .tts_sapi import english_sentence
        if self.english and english_sentence(text):
            audio = self.english.synthesize(text)
            self.actual_voice = self.english.actual_voice
            return audio
        if not re.search(r'\w', text, flags=re.UNICODE):
            raise ValueError('TTS input contains no pronounceable text')
        if self.rate not in {'x-slow','slow','medium','fast','x-fast'} or self.pitch not in {'x-low','low','medium','high','x-high'}:
            raise ValueError('Unsupported Silero SSML rate/pitch')
        options={'text':text}
        if self.rate!='medium' or self.pitch!='medium':
            from html import escape
            options={'ssml_text':f'<speak><prosody rate="{self.rate}" pitch="{self.pitch}">{escape(text)}</prosody></speak>'}
        try:
            audio = self._load().apply_tts(**options, speaker=self.voice, sample_rate=self.sample_rate)
            self.actual_voice = self.voice
        except (ValueError, AssertionError, RuntimeError):
            if not self.fallback_voice or self.voice == self.fallback_voice: raise
            audio = self._load().apply_tts(**options, speaker=self.fallback_voice, sample_rate=self.sample_rate)
            self.actual_voice = self.fallback_voice
            import logging
            logging.getLogger(__name__).warning('TTS voice fallback: %s -> %s', self.voice, self.actual_voice)
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        return ramp(audio, self.sample_rate)

    def save_wav(self, text: str, path: Path) -> float:
        started = perf_counter()
        sf.write(path, self.synthesize(text), self.sample_rate)
        return perf_counter() - started

    def stop(self) -> None:
        self._stop.set()
        sd.stop()

    def speak(self, text: str) -> float:
        if self.muted or not text.strip():
            return 0.0
        self._stop.clear()
        chunks = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        first_start = 0.0
        started = perf_counter()
        device = find_device(self.output_device, input_device=False)
        with self._lock:
            for index, chunk in enumerate(chunks or [text]):
                if self._stop.is_set():
                    break
                audio = self.synthesize(chunk) * self.volume
                if self._stop.is_set():
                    break
                sd.play(audio, self.sample_rate, device=device, blocking=False)
                if index == 0:
                    first_start = perf_counter() - started
                    if self.performance:
                        self.performance.record("tts_start_latency", first_start, voice=self.voice)
                sd.wait()
        return first_start
