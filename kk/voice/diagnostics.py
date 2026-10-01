"""Explicit, short-lived capture of a real final voice turn for local diagnosis."""
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Timer
from time import monotonic
import json
import wave


SAVE_COMMANDS = {'сохрани последнюю ошибку распознавания', 'save last voice sample'}


def infer_intent(text, accepted=True):
    if not accepted: return 'uncertain'
    from kk.core.desktop_commands import parse_plan
    from kk.core.orchestrator import requests_action
    plan = parse_plan(text)
    if plan: return ','.join(step['kind'] for step in plan)
    if requests_action(text): return 'desktop_task'
    if '?' in text or text.casefold().startswith(('что ', 'как ', 'почему ', 'когда ', 'где ')):
        return 'question'
    return 'conversation'


class LastVoiceSample:
    def __init__(self, folder: Path, ttl_seconds=300):
        self.folder = Path(folder)
        self.ttl_seconds = ttl_seconds
        self.lock = RLock()
        self.sample = None
        self._timer = None
        self._sequence = 0

    def _expire(self, sequence):
        with self.lock:
            if sequence == self._sequence:
                self.sample = None
                self._timer = None

    def remember(self, pcm: bytes, result, intent=''):
        with self.lock:
            if self._timer: self._timer.cancel()
            self._sequence += 1
            first_pass = (getattr(result, 'passes', None) or [{}])[0]
            self.sample = dict(pcm=bytes(pcm), raw_transcript=first_pass.get('raw_transcript') or result.raw_transcript or result.text,
                final_transcript=result.text, confidence=result.confidence, intent=intent or result.text,
                timestamp=datetime.now(timezone.utc).isoformat(), monotonic=monotonic())
            self._timer = Timer(self.ttl_seconds, self._expire, args=(self._sequence,))
            self._timer.daemon = True
            self._timer.start()

    def save(self):
        with self.lock:
            sample = self.sample
            self.sample = None
            if self._timer: self._timer.cancel()
            self._timer = None
        if not sample or monotonic() - sample['monotonic'] >= self.ttl_seconds:
            return None
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        audio_path = self.folder / f'{stamp}.wav'
        metadata_path = self.folder / f'{stamp}.json'
        with wave.open(str(audio_path), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(sample['pcm'])
        metadata = {k: v for k, v in sample.items() if k not in {'pcm', 'monotonic'}}
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        return metadata_path
