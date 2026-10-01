"""Exercise real KK Orb HWNDs on both monitors without changing user positions."""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep, monotonic
from threading import Thread
import json
import ctypes
from ctypes import wintypes
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from kk.config import Config
    from kk.core.state import StateMachine, AgentState
    from kk.ui.overlay import Overlay
    from kk.ui.orb_model import save_positions
    from kk.ui.orb_window import u
    from kk.tools.windows.native import list_monitors
    from PIL import ImageGrab
    result, errors = [], []
    monitors = list_monitors()
    for monitor in monitors:
        with TemporaryDirectory(prefix='kk-orb-check-') as folder:
            config = Config(Path(folder), {'ui': {'orb_size': 144, 'fullscreen_auto_hide': False}})
            save_positions(config.root/'data/orb_positions.json', {'_last_monitor': monitor['monitor_id']})
            state = StateMachine()
            overlay = Overlay(state, config)
            foreground = u.GetForegroundWindow()
            def run():
                try:
                    overlay._run()
                except Exception as exc:
                    errors.append(repr(exc))
            thread = Thread(target=run, daemon=True)
            thread.start()
            try:
                deadline = monotonic()+5
                while not overlay.hwnd and not errors and monotonic() < deadline:
                    sleep(.05)
                sleep(.2)
                assert overlay.hwnd and not errors, errors
                hwnd = overlay.hwnd
                rect = wintypes.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(rect))
                bounds = [rect.left, rect.top, rect.right, rect.bottom]
                row = dict(monitor=monitor['monitor_id'], bounds=bounds,
                    no_focus_stolen=u.GetForegroundWindow()==foreground,
                    no_activate_style=bool(u.GetWindowLongW(hwnd, -20) & 0x08000000),
                    inside_monitor=monitor['left'] <= rect.left < rect.right <= monitor['right']
                        and monitor['top'] <= rect.top < rect.bottom <= monitor['bottom'],
                    idle_visible=bool(u.IsWindowVisible(hwnd)))
                state.set(AgentState.USER_SPEAKING, 'Private transcript must not be drawn')
                state.audio_level = .04
                sleep(.2)
                ImageGrab.grab(bbox=tuple(bounds), all_screens=True).save(ROOT/'data'/f'kk_orb_monitor_{monitor["index"]}.png')
                state.set_orb_visible(False)
                sleep(.2)
                row['manual_hide'] = not bool(u.IsWindowVisible(hwnd))
                row['voice_state_preserved'] = state.state == AgentState.USER_SPEAKING
                state.set_orb_visible(True)
                sleep(.2)
                row['manual_show'] = bool(u.IsWindowVisible(hwnd))
                result.append(row)
                assert all(value for key, value in row.items() if isinstance(value, bool)), row
            finally:
                overlay.stop()
                thread.join(3)
            assert not thread.is_alive(), 'Orb did not stop'
    assert not errors, errors
    (ROOT/'data/companion_orb_acceptance.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
