"""Background device/topology and resume recovery for the tray process."""
from __future__ import annotations

from threading import Event, Thread
from time import monotonic

from kk.core.state import AgentState
from kk.tools.windows.native import list_monitors
from kk.voice.audio_devices import list_audio_devices


class BackgroundRecoverySupervisor:
    def __init__(self, runtime, voice, microphone, interval=2.0, resume_gap=12.0):
        self.runtime, self.voice = runtime, voice
        self.microphone = str(microphone or '').casefold()
        self.interval, self.resume_gap = interval, resume_gap
        self.stop_event = Event()
        self.thread = None
        self.last_tick = monotonic()
        self.audio_available = None
        self.monitor_signature = None
        self.events = []

    def _audio_present(self, devices):
        candidates = [d for d in devices if d.get('input_channels',0)>0]
        if not self.microphone:
            return any(d.get('default_input') for d in candidates)
        return any(self.microphone.split("|",1)[-1] in str(d.get('name','')).casefold() for d in candidates)

    @staticmethod
    def _monitor_signature(monitors):
        return tuple((m.get('monitor_id'),m.get('left'),m.get('top'),m.get('right'),
                      m.get('bottom'),m.get('dpi',{}).get('x')) for m in monitors)

    def _record(self, event, **details):
        self.events.append({'event':event,**details})
        del self.events[:-100]

    def recover_after_resume(self):
        self.voice.loop.interrupt()
        self.voice.stt.cancel()
        self.runtime.context.reset('resume')
        available = self.runtime.provider.is_available()
        self._record('resume_recovery',ollama_available=available)
        if not available:
            self.runtime.state.set(AgentState.ERROR,'Ollama недоступна после resume; повторю при запросе')
        elif self.audio_available is not False:
            self.runtime.state.set(AgentState.IDLE,'Восстановлено после сна')

    def tick(self, now=None, audio_devices=None, monitors=None):
        now = monotonic() if now is None else now
        if now-self.last_tick > self.resume_gap:
            self.recover_after_resume()
        self.last_tick = now
        devices = list_audio_devices() if audio_devices is None else audio_devices
        present = self._audio_present(devices)
        if self.audio_available is True and not present:
            self.voice.loop.interrupt()
            self.voice.stt.cancel()
            self.runtime.state.set(AgentState.ERROR,'Микрофон недоступен; ожидаю reconnect')
            self._record('microphone_disconnected')
        elif self.audio_available is False and present:
            self.voice.stt.cancel_event.clear()
            self.runtime.state.set(AgentState.IDLE,'Микрофон снова доступен')
            self._record('microphone_reconnected')
        self.audio_available = present

        current_monitors = list_monitors() if monitors is None else monitors
        signature = self._monitor_signature(current_monitors)
        if self.monitor_signature is not None and signature != self.monitor_signature:
            self.voice.loop.interrupt()
            self.runtime.context.reset('monitor_topology_changed')
            self._record('monitor_topology_changed',monitors=len(current_monitors))
        self.monitor_signature = signature

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        def run():
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    self._record('recovery_probe_error',error=type(exc).__name__)
                self.stop_event.wait(self.interval)
        self.thread = Thread(target=run,name='KKRecovery',daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
