"""Controlled audio-file input at the production AudioInput boundary.

Usage:
    with replay_audio_input(source='controlled_sapi_replay') as source:
        # Start VoiceLoop.run normally; its output/AEC/STT are unchanged.
        source.feed_wav(path, label='wake_question')
        source.wait_empty(30)

This proves the file -> production capture/VAD/STT/handler path, not microphone
quality or room echo. Only the coordinator may run the associated voice runtime.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from math import gcd
from pathlib import Path
from threading import Event, Lock
from time import monotonic, perf_counter, sleep
from unittest.mock import patch
import numpy as np


class ReplaySource:
    def __init__(self, source='controlled_audio_file_replay'):
        if not source or not source.startswith('controlled_'):
            raise ValueError('An explicit controlled_ source label is required')
        self.source = source
        self.segments = deque()
        self.lock = Lock()
        self.empty = Event()
        self.empty.set()
        self.records = []
        self.sample_rate = 16000
        self.closed = Event()

    def feed_pcm(self, pcm, *, label, sample_rate=16000):
        if sample_rate != 16000 or len(pcm) % 2:
            raise ValueError('Replay PCM must be signed little-endian int16 mono at 16000 Hz')
        if not label or not pcm:
            raise ValueError('A nonempty audio segment and provenance label are required')
        record = dict(source=self.source, label=label, queued_at=monotonic(),
                      sample_rate=16000, frames=len(pcm)//2, read_frames=0,
                      first_read_at=None, last_read_at=None,
                      first_read_perf_counter=None, last_read_perf_counter=None)
        with self.lock:
            self.records.append(record)
            self.segments.append([bytes(pcm), 0, record])
            self.empty.clear()
        return record

    def feed_wav(self, path, *, label):
        import soundfile as sf
        from scipy.signal import resample_poly
        path = Path(path)
        audio, rate = sf.read(path, dtype='float32', always_2d=True)
        if audio.shape[1] != 1:
            raise ValueError('Controlled replay requires a mono file; channels are not mixed implicitly')
        audio = audio[:, 0]
        if rate != 16000:
            divisor = gcd(rate, 16000)
            audio = resample_poly(audio, 16000//divisor, rate//divisor)
        pcm = np.clip(np.rint(audio*32768), -32768, 32767).astype('<i2').tobytes()
        record = self.feed_pcm(pcm, label=label)
        record.update(path=str(path.resolve()), original_sample_rate=rate)
        return record

    def read(self, frames):
        result = bytearray()
        with self.lock:
            while self.segments and len(result) < frames*2:
                pcm, offset, record = self.segments[0]
                count = min(frames*2-len(result), len(pcm)-offset)
                result.extend(pcm[offset:offset+count])
                self.segments[0][1] += count
                record['first_read_at'] = record['first_read_at'] or monotonic()
                record['last_read_at'] = monotonic()
                record['first_read_perf_counter'] = record['first_read_perf_counter'] or perf_counter()
                record['last_read_perf_counter'] = perf_counter()
                record['read_frames'] += count//2
                if offset+count == len(pcm):
                    self.segments.popleft()
            if not self.segments:
                self.empty.set()
        result.extend(bytes(frames*2-len(result)))
        return bytes(result)

    def wait_empty(self, timeout=60):
        if not self.empty.wait(timeout):
            raise TimeoutError('Replay input was not consumed before timeout')


class QueueAudioInput:
    """AudioInput-compatible paced reader; no PortAudio input stream is opened."""
    def __init__(self, source, device, frames, sample_rate=16000):
        if sample_rate != 16000:
            raise ValueError('Production replay boundary expects 16000 Hz')
        self.source, self.frames = source, frames
        self.native_rate = sample_rate
        self.device_name = 'controlled replay: '+source.source
        self.selected_channel = 0
        self.channel_rms = [0.0]
        self.peak = 0.0
        self.next_at = monotonic()
        self.last_packet = self.next_at

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def read(self, frames):
        self.next_at = max(self.next_at, monotonic()) + frames/16000
        delay = self.next_at-monotonic()
        if delay > 0:
            sleep(delay)
        pcm = self.source.read(frames)
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32)/32768
        self.channel_rms = [float(np.sqrt(np.mean(values*values)))]
        self.peak = float(np.max(np.abs(values))) if len(values) else 0.0
        self.last_packet = monotonic()
        return pcm, False


@contextmanager
def replay_audio_input(source='controlled_audio_file_replay'):
    """Replace only input PCM and input-device lookup; real output stays intact."""
    from kk.voice.audio_devices import find_device
    replay = ReplaySource(source)
    def factory(device, frames, sample_rate=16000):
        return QueueAudioInput(replay, device, frames, sample_rate)
    def select_device(value, *, input_device):
        return None if input_device else find_device(value, input_device=False)
    with patch('kk.voice.audio_input.AudioInput', factory), \
         patch('kk.voice.audio_devices.find_device', select_device):
        try:
            yield replay
        finally:
            replay.closed.set()
