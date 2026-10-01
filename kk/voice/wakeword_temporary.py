from __future__ import annotations

from threading import Event
from typing import Callable

from .stt_faster_whisper import FasterWhisperSTT
from .tts_silero import SileroTTS
from kk.core.agent import Agent
from kk.core.state import AgentState, StateMachine


class TemporaryWakeWord:
    """STT-based prototype. This is deliberately not presented as native wake-word."""

    def __init__(self, stt: FasterWhisperSTT, phrase: str = "оникс"):
        self.stt = stt
        self.phrase = phrase.casefold()
        self.stop_event = Event()
        from .wakeword import STTWakeWordProvider
        self.detector = STTWakeWordProvider(phrase)

    def stop(self) -> None:
        self.stop_event.set()
        self.stt.cancel()

    def run(self, on_wake: Callable[[str], None]) -> None:
        self.stop_event.clear()
        while not self.stop_event.is_set():
            try:
                transcription = self.stt.listen(max_seconds=5, wait_for_speech_seconds=30)
            except (TimeoutError, InterruptedError):
                continue
            if self.detector.detect_text(transcription.text):
                on_wake(transcription.text)

    def run_command_loop(self, agent: Agent, tts: SileroTTS, state: StateMachine) -> None:
        """Single-microphone prototype: wake phrase, then command, then TTS."""
        self.stop_event.clear()
        state.set(AgentState.IDLE, f"Ожидаю: {self.phrase}")
        while not self.stop_event.is_set():
            try:
                heard = self.stt.listen(max_seconds=5, wait_for_speech_seconds=30)
                folded = heard.text.casefold()
                if not self.detector.detect_text(folded):
                    continue
                command = self.detector.command(heard.text)
                if not command:
                    state.set(AgentState.LISTENING, "Слушаю команду")
                    command = self.stt.listen().text
                if not command:
                    continue
                from .loop import is_stop
                if is_stop(command):
                    tts.stop()
                    agent.cancel()
                    agent.orchestrator.tools.policy.trigger_stop()
                    state.set(AgentState.STOPPED, 'Голосовая остановка')
                    self.stop()
                    break
                state.set(AgentState.THINKING, command)
                answer = agent.ask(command)
                state.set(AgentState.SPEAKING, answer[:160])
                tts.speak(answer)
                state.set(AgentState.IDLE, f"Ожидаю: {self.phrase}")
            except (TimeoutError, InterruptedError):
                state.set(AgentState.IDLE, f"Ожидаю: {self.phrase}")
