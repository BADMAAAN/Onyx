from typing import Protocol


class TextToSpeech(Protocol):
    def speak(self, text: str) -> None: ...

