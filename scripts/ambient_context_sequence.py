"""Ten-minute observed desktop sequence and context-answer acceptance."""
from __future__ import annotations

import ctypes
import json
import logging
from pathlib import Path
import shutil
import subprocess
from time import monotonic, sleep, time

from kk.config import Config
from kk.runtime import build_runtime
from kk.tools.windows.native import foreground_window, list_monitors, window_metadata
from kk.tools.windows.windows import focus_window_handle, list_visible_windows


ROOT=Path(__file__).resolve().parents[1]


def find(process,include_minimized=False):
    return next((w for w in list_visible_windows() if w.get('process','').casefold()==process.casefold() and
                 (include_minimized or not w.get('minimized'))),None)


def wait_for(process,seconds=20):
    until=monotonic()+seconds
    while monotonic()<until:
        w=find(process,True)
        if w:
            return w
        sleep(.25)
    raise LookupError(process)


def move(hwnd,monitor):
    x,y=monitor['left']+80,monitor['top']+80
    ctypes.windll.user32.SetWindowPos(hwnd,0,x,y,1000,700,0x0004|0x0010)


def focus_retry(process,hwnd):
    error=None
    for _ in range(8):
        candidate=next((w for w in list_visible_windows() if w.get('hwnd')==hwnd and
                        w.get('process','').casefold()==process.casefold()),None)
        if candidate is None:
            candidate=find(process,True)
        if candidate is None:
            sleep(.5)
            continue
        hwnd=candidate['hwnd']
        if candidate.get('minimized'):
            ctypes.windll.user32.ShowWindow(hwnd,9)
        try:
            return focus_window_handle(hwnd)
        except (LookupError,OSError) as exc:
            error=exc
            sleep(.5)
    raise OSError(f'{process} foreground not verified: {error}')


def main(dwell=76):
    original=foreground_window()
    monitors=list_monitors()
    if len(monitors)<2:
        return {'error':'two monitors unavailable'}
    runtime=build_runtime(Config.load(ROOT/'config.toml'),logging.getLogger('context-sequence'),allow_offline=True)
    manager=runtime.context
    manager.start()
    launched=[]
    originals={}
    settings_was_minimized=False
    start=monotonic()
    actual=[]
    try:
        if not find('Code.exe'):
            command=shutil.which('code.cmd')
            subprocess.Popen(['cmd.exe','/c',command,str(ROOT)],creationflags=subprocess.CREATE_NO_WINDOW)
            launched.append('Code.exe')
        if not find('Notepad.exe'):
            subprocess.Popen(['notepad.exe'])
            launched.append('Notepad.exe')
        code=wait_for('Code.exe'); note=wait_for('Notepad.exe')
        ayu=wait_for('AyuGram.exe'); browser=find('firefox.exe') or find('opera.exe')
        settings=find('ApplicationFrameHost.exe',True)
        if not browser or not settings:
            return {'error':'browser or Settings unavailable'}
        settings_was_minimized=bool(settings.get('minimized'))
        if settings.get('minimized'):
            ctypes.windll.user32.ShowWindow(settings['hwnd'],9)
        windows=[code,note,ayu,browser,settings]
        originals={w['hwnd']:w['bounds'] for w in windows if w['process'] not in launched}
        for w in windows:
            move(w['hwnd'],monitors[1])
        sequence=[('Code.exe',code),('AyuGram.exe',ayu),('browser',browser),('Code.exe',code),
                  ('Notepad.exe',note),('Notepad moved within M2',note),('Settings',settings),('Code.exe',code)]
        for label,w in sequence:
            if not ctypes.windll.user32.IsWindow(w['hwnd']):
                if w['process']=='Notepad.exe':
                    subprocess.Popen(['notepad.exe'])
                    note=wait_for('Notepad.exe')
                    w=note
                    if 'Notepad.exe' not in launched:
                        launched.append('Notepad.exe')
                    move(note['hwnd'],monitors[1])
                else:
                    w=wait_for(w['process'])
            move(w['hwnd'],monitors[1])
            if label=='Notepad moved within M2':
                ctypes.windll.user32.SetWindowPos(note['hwnd'],0,monitors[1]['left']+300,
                    monitors[1]['top']+180,1000,700,0x0004|0x0010)
            if label=='Settings' and 'Notepad.exe' in launched:
                ctypes.windll.user32.PostMessageW(note['hwnd'],0x0010,0,0)
            focus_retry(w['process'],w['hwnd'])
            manager.sample(screen=False)
            fg=foreground_window()
            actual.append({'step':label,'time':time(),'process':fg.get('process'),
                           'monitor_id':fg.get('monitor_id'),'hwnd':fg.get('hwnd')})
            print(json.dumps(actual[-1],ensure_ascii=False),flush=True)
            sleep(dwell)
        summary=manager.recent(660)
        answer=runtime.agent.ask('Что я делал последние 10 минут?')
        return {'elapsed_seconds':round(monotonic()-start,1),'actual':actual,
                'focus_sequence':summary['summary']['focus_sequence'],
                'total_events':summary['total_events'],'answer':answer}
    finally:
        # This acceptance run is confined to monitor 2 by user request.
        if settings_was_minimized and settings and ctypes.windll.user32.IsWindow(settings['hwnd']):
            ctypes.windll.user32.ShowWindow(settings['hwnd'],6)
        for process in launched:
            w=find(process)
            if w and ctypes.windll.user32.IsWindow(w['hwnd']):
                ctypes.windll.user32.PostMessageW(w['hwnd'],0x0010,0,0)
        if original.get('hwnd') and ctypes.windll.user32.IsWindow(original['hwnd']):
            try: focus_window_handle(original['hwnd'])
            except Exception: pass
        manager.stop(); runtime.memory.close()


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    print(json.dumps(main(float(sys.argv[1]) if len(sys.argv)>1 else 76),ensure_ascii=False),flush=True)
