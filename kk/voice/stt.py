from typing import Protocol


class SpeechToText(Protocol):
    def transcribe(self, audio_path: str) -> str: ...

