"""Visible two-monitor smoke test. Restores the original focus and AyuGram geometry."""
from __future__ import annotations

import ctypes
import json
import logging
from pathlib import Path
import shutil
import subprocess
from time import monotonic, sleep

from kk.config import Config
from kk.runtime import build_runtime
from kk.tools.windows.native import foreground_window, list_monitors, window_metadata
from kk.tools.windows.windows import focus_window_handle, list_visible_windows


ROOT = Path(__file__).resolve().parents[1]


def find_process(name):
    return next((w for w in list_visible_windows() if w.get('process','').casefold() == name.casefold() and not w.get('minimized')), None)


def put(hwnd, bounds):
    left, top, right, bottom = bounds
    if not ctypes.windll.user32.SetWindowPos(hwnd, 0, left, top, right-left, bottom-top, 0x0004|0x0010):
        raise OSError('SetWindowPos failed')
    return window_metadata(hwnd)


def wait_for(name, seconds=12):
    end = monotonic()+seconds
    while monotonic()<end:
        found = find_process(name)
        if found:
            return found
        sleep(.25)
    return None


def focus_process(name, preferred_hwnd=None):
    last_error = None
    for _ in range(8):
        candidate = next((w for w in list_visible_windows() if w.get('process','').casefold()==name.casefold() and
                          (preferred_hwnd is None or w['hwnd']==preferred_hwnd)), None)
        if candidate is not None:
            try:
                return focus_window_handle(candidate['hwnd'])
            except (LookupError, OSError) as exc:
                last_error = exc
        sleep(.25)
    raise LookupError(f'{name} window unavailable during live test: {last_error}')


def main(hold_seconds=60):
    original = foreground_window()
    ayu = find_process('AyuGram.exe')
    if not ayu:
        return {'error':'AyuGram window not available'}
    original_ayu_bounds = ayu['bounds']
    config = Config.load(ROOT/'config.toml')
    runtime = build_runtime(config, logging.getLogger('ambient-live'), allow_offline=True)
    vision_calls = [0]
    if runtime.screen.provider:
        original_ask = runtime.screen.provider._ask
        def counted_vision(*args, **kwargs):
            vision_calls[0] += 1
            return original_ask(*args, **kwargs)
        runtime.screen.provider._ask = counted_vision
    manager = runtime.context
    manager.screen_interval = 0  # explicit test captures at each controlled transition
    results = {}
    code = find_process('Code.exe')
    launched_code = code is None
    original_code_bounds = code['bounds'] if code else None
    notepad = None
    launched_notepad = False
    try:
        if code is None:
            command = shutil.which('code.cmd')
            if not command:
                return {'error':'VS Code command unavailable'}
            subprocess.Popen(['cmd.exe','/c',command,str(ROOT)], creationflags=subprocess.CREATE_NO_WINDOW)
            code = wait_for('Code.exe',20)
        if code is None:
            return {'error':'VS Code window unavailable'}
        monitors = list_monitors()
        one, two = monitors[0], monitors[1]
        put(code['hwnd'],[one['left']+100,one['top']+80,one['left']+1100,one['top']+800])
        put(ayu['hwnd'],[two['left']+100,two['top']+80,two['left']+1100,two['top']+800])
        focus_process('Code.exe',code['hwnd'])
        manager.sample(screen=True)
        answer_a = runtime.agent.ask('что открыто на обоих экранах?')
        results['A'] = {'pass': 'Code' in answer_a and 'AyuGram' in answer_a and 'Монитор 2' in answer_a,
                        'code_monitor':window_metadata(code['hwnd'])['monitor_index'],
                        'ayugram_monitor':window_metadata(ayu['hwnd'])['monitor_index']}
        print('A',json.dumps(results['A'],ensure_ascii=False),flush=True)

        # Hold VS Code foreground long enough to create a measurable activity span.
        sleep(hold_seconds)
        manager.sample()
        focus_process('AyuGram.exe',ayu['hwnd'])
        manager.sample()
        sleep(2)
        focus_process('Code.exe',code['hwnd'])
        manager.sample()
        answer_b = runtime.agent.ask('что я сейчас делал?')
        results['B'] = {'pass':'Code.exe' in answer_b and 'AyuGram.exe' in answer_b,
                        'answer':answer_b}
        print('B',json.dumps(results['B'],ensure_ascii=False),flush=True)

        before_notepad = {w['hwnd'] for w in list_visible_windows() if w.get('process','').casefold() == 'notepad.exe'}
        opened = runtime.tools.execute('open_application',{'name':'notepad'})
        deadline = monotonic()+10
        while monotonic()<deadline:
            notepad = next((w for w in list_visible_windows() if w.get('process','').casefold()=='notepad.exe' and w['hwnd'] not in before_notepad), None)
            if notepad:
                break
            sleep(.2)
        launched_notepad = notepad is not None
        if notepad:
            manager.sample()
            put(notepad['hwnd'],[one['left']+100,one['top']+100,one['left']+1400,one['top']+900])
            manager.sample(screen=True)
            put(notepad['hwnd'],[two['left']+100,two['top']+100,two['left']+1400,two['top']+900])
            manager.sample(screen=True)
            transitions = [e for e in manager.timeline.recent() if e['event']=='MONITOR_CHANGED' and e['window'].get('hwnd')==notepad['hwnd']]
            results['C'] = {'pass':bool(transitions), 'notepad_monitor':window_metadata(notepad['hwnd'])['monitor_index']}
            sequence = [e['event'] for e in manager.timeline.recent() if e['window'].get('hwnd')==notepad['hwnd'] or e['event']=='KK_ACTION']
            results['E'] = {'pass': opened.ok and 'KK_ACTION' in sequence and 'WINDOW_OPENED' in sequence and
                            window_metadata(notepad['hwnd'])['monitor_index']==2, 'sequence':sequence[-8:]}
        else:
            results['C'] = {'pass':False,'reason':'Notepad window not found'}
            results['E'] = {'pass':False,'reason':opened.error}
        print('C',json.dumps(results['C'],ensure_ascii=False),flush=True)
        print('E',json.dumps(results['E'],ensure_ascii=False),flush=True)
        results['screen_diff'] = {m['index']: sum(change['monitor_id']==m['monitor_id'] for change in manager.screen_changes) for m in monitors}
        print('SCREEN_DIFF',json.dumps(results['screen_diff'],ensure_ascii=False),flush=True)

        focus_process('Code.exe',code['hwnd']); manager.sample()
        focus_process('AyuGram.exe',ayu['hwnd']); manager.sample()
        answer_d = runtime.agent.ask('верни меня туда, где я был до Telegram')
        observed = foreground_window()
        results['D'] = {'pass':observed['hwnd']==code['hwnd'], 'focused_process':observed.get('process'), 'answer':answer_d}
        print('D',json.dumps(results['D'],ensure_ascii=False),flush=True)
        results['qwen3_vl_calls'] = vision_calls[0]
        return results
    finally:
        try:
            put(ayu['hwnd'],original_ayu_bounds)
        except Exception:
            pass
        try:
            if code and original_code_bounds and not launched_code:
                put(code['hwnd'],original_code_bounds)
        except Exception:
            pass
        try:
            if original.get('hwnd'):
                focus_window_handle(original['hwnd'])
        except Exception:
            pass
        if launched_notepad and notepad:
            ctypes.windll.user32.PostMessageW(notepad['hwnd'],0x0010,0,0)
        if launched_code and code:
            ctypes.windll.user32.PostMessageW(code['hwnd'],0x0010,0,0)
        manager.stop()
        runtime.memory.close()


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    print(json.dumps(main(float(sys.argv[1]) if len(sys.argv)>1 else 60),ensure_ascii=False,indent=2),flush=True)
