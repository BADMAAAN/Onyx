from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from threading import Thread, Timer

from PIL import Image, ImageDraw
import pystray

from kk.config import Config
from kk.core.state import AgentState
from kk.runtime import Runtime
from kk.voice.factory import VoiceServices

from .overlay import Overlay


class TrayController:
    def __init__(self, config: Config, runtime: Runtime, voice: VoiceServices):
        self.config = config
        self.runtime = runtime
        self.voice = voice
        self.overlay = Overlay(runtime.state, config, microphone=voice.loop)
        self.vision_enabled = runtime.screen.provider is not None
        self._vision_provider = runtime.screen.provider
        self.icon = pystray.Icon("Onyx", self._icon_image(), "Onyx Personal Agent", self._menu())

    @staticmethod
    def _icon_image() -> Image.Image:
        branded=Path(__file__).resolve().parents[2]/'onyx_desktop/assets/pearl.png'
        if branded.is_file():return Image.open(branded).convert('RGBA').resize((64,64),Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (64, 64), (20, 20, 20, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((6, 6, 58, 58), fill=(45, 140, 240, 255))
        draw.text((15, 21), "O", fill="white")
        return image

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda _item: f"Onyx status: {self.runtime.state.state.value}", None, enabled=False),
            pystray.MenuItem("Microphone: record / commit", lambda _icon=None, _item=None: self.voice.loop.mic_toggle()),
            pystray.MenuItem("Start recording", lambda _icon=None, _item=None: self.voice.loop.mic_start()),
            pystray.MenuItem("Commit recording and execute", lambda _icon=None, _item=None: self.voice.loop.mic_commit()),
            pystray.MenuItem("Cancel recording", lambda _icon=None, _item=None: self.voice.loop.mic_cancel()),
            pystray.MenuItem("Save last voice sample", lambda _icon=None, _item=None: self.voice.loop.save_last_voice_sample()),
            pystray.MenuItem("Allow Edge Presence", self._toggle_orb, checked=lambda _item: not self.runtime.state.user_visual_hidden),
            pystray.MenuItem("Pause microphone", self._toggle_microphone, checked=lambda _item: self.voice.loop.paused),
            pystray.MenuItem("Voice on/off", self._toggle_voice, checked=lambda _item: not self.voice.tts.muted),
            pystray.MenuItem("Vision on/off", self._toggle_vision, checked=lambda _item: self.vision_enabled),
            pystray.MenuItem("Pause ambient monitoring", self._toggle_ambient, checked=lambda _item: self.runtime.context.paused),
            pystray.MenuItem("Открыть приложение", self._open_app),
            pystray.MenuItem("Open console", self._open_console),
            pystray.MenuItem("Open logs", lambda _icon=None, _item=None: os.startfile(self.config.root / "logs")),
            pystray.MenuItem("Stop current task", lambda _icon=None, _item=None: self.voice.loop.interrupt(source='tray_stop')),
            pystray.MenuItem("Emergency Stop", self._emergency_stop),
            pystray.MenuItem("Exit", self._exit),
        )

    def _toggle_microphone(self, _icon=None, _item=None):
        self.voice.loop.paused = not self.voice.loop.paused
        if self.voice.loop.capture:
            if self.voice.loop.paused:self.voice.loop.capture.paused.set()
            else:self.voice.loop.capture.paused.clear()
        if self.voice.loop.paused:
            self.voice.loop.interrupt()
            self.voice.stt.cancel()
            self.runtime.state.set(AgentState.PAUSED, 'Микрофон приостановлен')
        else:
            self.runtime.policy.clear_stop()
            self.runtime.state.set(AgentState.IDLE)

    def _toggle_orb(self, _icon=None, _item=None):
        self.runtime.state.set_orb_visible(not self.runtime.state.orb_visible)

    def _toggle_voice(self, _icon=None, _item=None):
        self.voice.tts.muted = not self.voice.tts.muted

    def _toggle_vision(self, _icon=None, _item=None):
        self.vision_enabled = not self.vision_enabled
        self.runtime.screen.provider = self._vision_provider if self.vision_enabled else None

    def _toggle_ambient(self, _icon=None, _item=None):
        self.runtime.context.pause(not self.runtime.context.paused)

    def _open_app(self, _icon=None, _item=None):
        from .desktop_launch import show
        show(self.config.root)

    def _open_console(self, _icon=None, _item=None):
        executable = Path(sys.executable).with_name('python.exe')
        subprocess.Popen([str(executable), "-m", "app.main", "--console-client"],
                         cwd=self.config.root, creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _emergency_stop(self, _icon=None, _item=None):
        self.voice.loop.emergency_stop()

    def _exit(self, _icon=None, _item=None):
        self.voice.loop.stop()
        self.overlay.stop()
        self.icon.stop()

    def run(self, test_seconds: float = 0) -> None:
        from kk.recovery import BackgroundRecoverySupervisor
        from kk.core.control import RuntimeControl
        control = RuntimeControl.background(self.config, self.runtime, self.voice, self._exit)
        control.start()
        # Only the backend owns global hooks. Existing PTT/Esc/Emergency Stop
        # keep their meanings; no new default chord or focus-on-wake behavior.
        desktop_hook = None
        show_hotkey = self.config.get('desktop.show_hotkey', '').strip()
        if show_hotkey:
            import keyboard
            import logging
            def chords(value):
                return {tuple(sorted(chord)) for step in keyboard.parse_hotkey_combinations(value) for chord in step}
            reserved = set().union(*(chords(value) for value in (self.voice.loop.hotkey,'esc','ctrl+alt+pause')))
            try:
                if chords(show_hotkey) & reserved:
                    raise ValueError('desktop.show_hotkey conflicts with an existing Onyx voice/stop shortcut')
                desktop_hook = keyboard.add_hotkey(show_hotkey,self._open_app,suppress=False)
            except (ValueError,KeyError) as exc:
                logging.getLogger('kk').warning('[DESKTOP] show hotkey disabled: %s',exc)
        recovery = BackgroundRecoverySupervisor(self.runtime,self.voice,
            self.config.get('voice.microphone'))
        recovery.start()
        self.voice.loop.wakeword = self.config.get('wakeword.enabled',True)
        thread = Thread(target=self.voice.loop.run, name="KKVoiceLoop", daemon=True)
        thread.start()
        # A failed voice worker must not leave a healthy-looking visual behind.
        Thread(target=lambda: (thread.join(), self.overlay.stop()),
               name='KKVoiceLiveness', daemon=True).start()
        if test_seconds > 0:
            Timer(test_seconds, self._exit).start()
        # The renderer supervisor owns this thread; tray has a Win32 message pump.
        tray_thread = Thread(target=self.icon.run, name='KKTray', daemon=True)
        tray_thread.start()
        try:
            self.overlay._run()
        finally:
            if desktop_hook is not None:
                import keyboard
                keyboard.remove_hotkey(desktop_hook)
            control.close()
            recovery.stop()
            self.voice.loop.stop()
            self.icon.stop()
            thread.join(timeout=5)
            tray_thread.join(timeout=3)
