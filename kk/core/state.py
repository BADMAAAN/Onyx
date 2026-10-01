from __future__ import annotations

from enum import Enum
from threading import RLock
from typing import Callable


class AgentState(str, Enum):
    MIC_READY = 'MIC_READY'
    RECORDING = 'RECORDING'
    COMMITTING = 'COMMITTING'
    SLEEPING = 'SLEEPING'
    USER_SPEAKING = 'USER_SPEAKING'
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    ACTING = "ACTING"
    SPEAKING = "SPEAKING"
    STOPPED = "STOPPED"
    PAUSED = 'PAUSED'
    ERROR = 'ERROR'


class StateMachine:
    def __init__(self, memory=None) -> None:
        self._state = AgentState.IDLE
        self._detail = ""
        self._orb_visible = True
        self._memory = memory
        stored = memory.get('ui.visual_visible') if memory is not None else None
        if stored and stored['value'] in {'true', 'false'}:
            self._orb_visible = stored['value'] == 'true'
        # Visual permission is independent of voice readiness and conversation.
        self._readiness = False
        self._activation_serial = 0
        self._activation_source = ''
        self._desktop_inline_live = False
        self._lock = RLock()
        self._listeners: list[Callable[[AgentState, str], None]] = []

    @property
    def orb_visible(self) -> bool:
        with self._lock:
            return self._orb_visible

    def set_orb_visible(self, visible: bool) -> dict:
        """Compatibility tool name; grants Edge permission, never enables Orb."""
        return self.set_visual_visible(visible)

    @property
    def user_visual_hidden(self) -> bool:
        return not self.orb_visible

    @property
    def readiness(self) -> bool:
        with self._lock:
            return self._readiness

    def set_readiness(self, ready: bool) -> None:
        with self._lock:
            self._readiness = bool(ready)

    def activate_visual(self, source: str = 'wake') -> int:
        """Nonblocking response to an explicit activation, not every turn/TTS."""
        with self._lock:
            self._activation_serial += 1
            self._activation_source = str(source)
            return self._activation_serial

    @property
    def desktop_inline_live(self) -> bool:
        with self._lock:
            return self._desktop_inline_live

    def set_desktop_inline_live(self, enabled: bool) -> None:
        """Session presentation only; never rewrite saved aura preferences."""
        with self._lock:
            self._desktop_inline_live = bool(enabled)

    def visual_snapshot(self) -> dict:
        with self._lock:
            return dict(user_visual_hidden=not self._orb_visible, readiness=self._readiness,
                        activation_serial=self._activation_serial,
                        activation_source=self._activation_source,
                        desktop_inline_live=self._desktop_inline_live)

    def set_visual_visible(self, visible: bool) -> dict:
        if type(visible) is not bool:
            raise TypeError('visible must be boolean')
        with self._lock:
            if self._memory is not None:
                self._memory.remember('ui.visual_visible', 'true' if visible else 'false')
            self._orb_visible = bool(visible)
            return {'visibility': 'visible' if self._orb_visible else 'hidden'}

    def forget_visual_preference(self):
        with self._lock:
            self._orb_visible = True  # configured default; no reinsert after Forget

    @property
    def state(self) -> AgentState:
        with self._lock:
            return self._state

    @property
    def detail(self) -> str:
        with self._lock:
            return self._detail

    def set(self, state: AgentState, detail: str = "") -> None:
        with self._lock:
            self._state, self._detail = state, detail
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(state, detail)
            except Exception:
                pass

    def subscribe(self, listener: Callable[[AgentState, str], None]) -> None:
        with self._lock:
            self._listeners.append(listener)
