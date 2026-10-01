"""Installed local Windows English voice -> PCM for the existing speech queue.

SAPI file synthesis only: playback, cancellation and AEC stay in SpeechQueue.
No downloads, cloud fallback or device-rate pitch changes.
API: https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ms722562(v=vs.85)
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import math
import re


def english_sentence(text):
    latin = re.findall(r'[A-Za-z]+', text)
    cyrillic = re.findall(r'[А-Яа-яЁё]+', text)
    # Technical names in a Russian sentence stay in the Russian voice.
    return ((len(latin) >= 3 and len(latin) > 3 * len(cyrillic)) or
            (not cyrillic and bool(re.match(r"(?i)^(?:opened|closed|typed|hello|yes|no|I|the|tea|please|don't)\b", text))))


class SapiEnglishTTS:
    def __init__(self, voice='Microsoft Zira Desktop', sample_rate=48000):
        self.voice, self.sample_rate = voice, sample_rate
        self.actual_voice = None
        self.native_rate = None

    def synthesize(self, text):
        import comtypes
        from comtypes.client import CreateObject
        import soundfile as sf
        from scipy.signal import resample_poly
        from .prosody import ramp
        comtypes.CoInitialize()
        try:
            speaker = CreateObject('SAPI.SpVoice', dynamic=True)
            tokens = speaker.GetVoices()
            selected = next((tokens.Item(i) for i in range(tokens.Count)
                             if self.voice.casefold() in tokens.Item(i).GetDescription().casefold()), None)
            if selected is None:
                raise RuntimeError('Configured local English voice is not installed: '+self.voice)
            speaker.Voice = selected
            self.actual_voice = selected.GetDescription()
            with TemporaryDirectory(prefix='onyx-tts-') as folder:
                path = Path(folder)/'speech.wav'
                stream = CreateObject('SAPI.SpFileStream', dynamic=True)
                stream.Open(str(path), 3, False)  # SSFMCreateForWrite
                try:
                    speaker.AudioOutputStream = stream
                    speaker.Speak(text, 0)
                finally:
                    stream.Close()
                audio, rate = sf.read(path, dtype='float32')
                self.native_rate = rate
                if audio.ndim > 1: audio = audio.mean(axis=1)
                if rate != self.sample_rate:
                    divisor = math.gcd(rate, self.sample_rate)
                    audio = resample_poly(audio, self.sample_rate//divisor, rate//divisor)
                return ramp(audio, self.sample_rate)
        finally:
            comtypes.CoUninitialize()
