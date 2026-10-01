"""Isolated visual check: no microphone, LLM, or changes to saved user positions.

Run: .venv/python.exe -m scripts.preview_orb --seconds 300
"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic
import tkinter as tk

from kk.config import Config
from kk.core.state import StateMachine, AgentState
from kk.ui.overlay import Overlay
from kk.ui.orb_model import load_positions, save_positions
from kk.ui.orb_window import foreground_info, u, w, c
from kk.tools.windows.native import list_monitors

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=300)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='kk-orb-preview-') as folder:
        config = Config(Path(folder), Config.load(ROOT/'config.toml').values)
        state = StateMachine()
        mic_events=[]
        class PreviewMic:
            def mic_start(self):
                mic_events.append('start'); state.set(AgentState.RECORDING)
            def mic_commit(self):
                mic_events.append('commit'); state.set(AgentState.COMMITTING)
            def mic_cancel(self):
                mic_events.append('cancel'); state.set(AgentState.IDLE)
            def mic_toggle(self):
                if state.state == AgentState.RECORDING: self.mic_commit()
                else: self.mic_start()
        mic=PreviewMic()
        overlay = Overlay(state, config, mic)
        errors = []
        def run():
            try:
                overlay._run()
            except Exception as exc:
                errors.append(repr(exc))
        root = tk.Tk()
        root.title('KK Orb validation')
        root.geometry('540x300+240+180')
        root.configure(bg='#101321')
        tk.Label(root, text='KK Orb · preview', bg='#101321', fg='#e9efff',
                 font=('Segoe UI', 20)).pack(pady=20)
        buttons = tk.Frame(root, bg='#101321')
        buttons.pack()
        for name in ('IDLE', 'LISTENING', 'THINKING', 'SPEAKING'):
            tk.Button(buttons, text=name, command=lambda s=name: state.set(AgentState(s))).pack(side='left', padx=4)
        state.audio_level, state.tts_level = .045, .07
        controls = tk.Frame(root, bg='#101321')
        controls.pack(pady=16)
        tk.Button(controls, text='Hide', command=lambda: state.set_orb_visible(False)).pack(side='left', padx=4)
        tk.Button(controls, text='Show', command=lambda: state.set_orb_visible(True)).pack(side='left', padx=4)
        tk.Button(controls, text='Fullscreen (Esc exits)', command=lambda: root.attributes('-fullscreen', True)).pack(side='left', padx=4)
        def monitor(index):
            monitors = list_monitors()
            if len(monitors) <= index:
                return
            chosen = monitors[index]
            positions = load_positions(config.root/'data/orb_positions.json')
            positions['_last_monitor'] = chosen['monitor_id']
            save_positions(config.root/'data/orb_positions.json', positions)
            overlay.stop()
            thread.join(3)
            restart()
        extra = tk.Frame(root, bg='#101321')
        extra.pack()
        tk.Button(extra, text='Monitor 1', command=lambda: monitor(0)).pack(side='left', padx=4)
        tk.Button(extra, text='Monitor 2', command=lambda: monitor(1)).pack(side='left', padx=4)
        def restart():
            nonlocal overlay, thread
            if overlay.thread or overlay.hwnd:
                overlay.stop()
                thread.join(3)
            overlay = Overlay(state, config, mic)
            thread = Thread(target=run, daemon=True)
            thread.start()
        tk.Button(extra, text='Restart orb', command=restart).pack(side='left', padx=4)
        root.bind('<Escape>', lambda _: root.attributes('-fullscreen', False))
        root.bind('<F11>', lambda _: root.attributes('-fullscreen', not root.attributes('-fullscreen')))
        tk.Label(root, text='Drag the orb. Positions are temporary in this preview.',
                 bg='#101321', fg='#a5b2cf').pack(pady=18)
        started = monotonic()
        thread = Thread(target=run, daemon=True)
        thread.start()
        history = []
        def report():
            if not root.winfo_exists():
                return
            hwnd = overlay.hwnd
            row = dict(seconds=round(monotonic()-started, 2), hwnd=hwnd,
                monitor=overlay.monitor_id, position=overlay.position, state=state.state.value,
                requested_visible=state.orb_visible,
                window_visible=bool(hwnd and u.IsWindowVisible(hwnd)),
                foreground=foreground_info().get('hwnd'), errors=list(errors),
                saved=load_positions(config.root/'data/orb_positions.json'), mic_events=list(mic_events))
            history.append(row)
            (ROOT/'data/kk_orb_native_acceptance.json').write_text(json.dumps(history, indent=2), encoding='utf-8')
            root.after(500, report)
        root.after(500, report)
        root.after(int(args.seconds*1000), root.destroy)
        try:
            root.mainloop()
        finally:
            overlay.stop()
            thread.join(3)
        if errors:
            raise RuntimeError(errors)


if __name__ == '__main__':
    main()
