from pathlib import Path
from typing import Protocol
import re
import numpy as np


class WakeWordProvider(Protocol):
    name: str
    def reset(self): ...


class STTWakeWordProvider:
    name = 'stt-fallback'

    def __init__(self, phrase='оникс'):
        self.pattern = re.compile(r'^\s*' + r'[\s-]+'.join(re.escape(w) for w in phrase.casefold().split()) + r'(?!\w)')
        if phrase.casefold() in {'оникс', 'onyx', 'кей кей'}:
            self.pattern = re.compile(r'^\s*(?:оникс|onyx|кей[\s-]+кей|kk|kay[\s-]+kay|key[\s-]+key)(?!\w)',re.I)

    def detect_text(self, text):
        return bool(self.pattern.search(text.casefold()))

    def command(self, text):
        match = self.pattern.search(text.casefold())
        return text[match.end():].strip(' ,.!?—-') if match else ''

    def reset(self):
        pass


class NativeWakeWordProvider:
    name = 'openwakeword-local'

    def __init__(self, model_path, threshold=.65, consecutive_frames=2):
        path = Path(model_path)
        if not path.is_file() or path.suffix != '.onnx':
            raise FileNotFoundError('Native Кей Кей model is not trained; use STT fallback')
        from openwakeword.model import Model
        self.model = Model(wakeword_models=[str(path)], inference_framework='onnx',
            melspec_model_path=str(path.parent/'melspectrogram.onnx'),
            embedding_model_path=str(path.parent/'embedding_model.onnx'))
        self.threshold = float(threshold)
        if not 0 < self.threshold <= 1:
            raise ValueError('Invalid wake threshold')
        self.required = max(1, int(consecutive_frames))
        self.hits = 0

    def process(self, pcm):
        samples = np.frombuffer(pcm, dtype=np.int16) if isinstance(pcm, bytes) else np.asarray(pcm,dtype=np.int16)
        predictions = self.model.predict(samples)
        score = max(float(v) for v in predictions.values())
        self.hits = self.hits+1 if score >= self.threshold else 0
        return {'activated': self.hits >= self.required, 'score': score}

    def reset(self):
        self.model.reset()
        self.hits = 0


WakeWordDetector = WakeWordProvider
