"""Controlled two-monitor move/lifecycle acceptance; restore existing windows."""
from __future__ import annotations

import ctypes
import json
import subprocess
import shutil
from tempfile import TemporaryDirectory
from pathlib import Path
from time import sleep

from kk.ambient import AmbientContextManager
from kk.tools.windows.native import list_monitors, window_metadata
from kk.tools.windows.windows import list_visible_windows


def find(process):
    return next((w for w in list_visible_windows() if w.get('process','').casefold()==process.casefold() and not w.get('minimized')),None)


def move(hwnd, monitor):
    l,t = monitor['left']+100,monitor['top']+100
    if not ctypes.windll.user32.SetWindowPos(hwnd,0,l,t,l+1000, t+700,0x0004|0x0010):
        raise OSError('SetWindowPos failed')
    sleep(.15)
    return window_metadata(hwnd)


def main():
    monitors=list_monitors()
    if len(monitors)<2:
        return {'error':'two monitors unavailable'}
    processes=['Code.exe','AyuGram.exe','Notepad.exe','ApplicationFrameHost.exe','firefox.exe','opera.exe']
    existing={name:find(name) for name in processes}
    launched=[]
    if not existing['Notepad.exe']:
        subprocess.Popen(['notepad.exe'])
        launched.append('Notepad.exe')
    if not existing['ApplicationFrameHost.exe']:
        subprocess.Popen(['cmd.exe','/c','start','ms-settings:'],creationflags=subprocess.CREATE_NO_WINDOW)
        launched.append('ApplicationFrameHost.exe')
    if not existing['Code.exe'] and shutil.which('code.cmd'):
        subprocess.Popen(['cmd.exe','/c','code.cmd',str(Path(__file__).resolve().parents[1])],creationflags=subprocess.CREATE_NO_WINDOW)
        launched.append('Code.exe')
    sleep(5)
    windows=[find(name) for name in processes]
    windows=[w for w in windows if w]
    originals={w['hwnd']:w['bounds'] for w in windows if w['process'] not in launched}
    with TemporaryDirectory() as folder:
        manager=AmbientContextManager(Path(folder)/'ambient.db',uia=None)
        manager.sample(screen=False)
        checks=[]
        try:
            for i in range(20):
                w=windows[i%len(windows)]
                current=window_metadata(w['hwnd'])['monitor_id']
                target=next(m for m in monitors if m['monitor_id']!=current)
                observed=move(w['hwnd'],target)
                manager.sample(screen=False)
                checks.append({'process':w['process'],'target':target['monitor_id'],
                               'observed':observed['monitor_id'],
                               'pass':observed['monitor_id']==target['monitor_id']})
            lifecycle={}
            note=find('Notepad.exe')
            if note:
                hwnd=note['hwnd']
                ctypes.windll.user32.ShowWindow(hwnd,6)
                manager.sample(screen=False)
                lifecycle['minimized']=window_metadata(hwnd)['minimized']
                ctypes.windll.user32.ShowWindow(hwnd,9)
                manager.sample(screen=False)
                lifecycle['restored']=not window_metadata(hwnd)['minimized']
                if 'Notepad.exe' in launched:
                    ctypes.windll.user32.PostMessageW(hwnd,0x0010,0,0)
                    sleep(1)
                    manager.sample(screen=False)
                    lifecycle['closed']=not ctypes.windll.user32.IsWindow(hwnd)
            events=manager.timeline.recent(900,800)
            result={'checks':checks,'all_pass':all(c['pass'] for c in checks),
                    'monitor_change_events':sum(e['event']=='MONITOR_CHANGED' for e in events),
                    'lifecycle':lifecycle,'monitors':monitors}
        finally:
            for hwnd,b in originals.items():
                if ctypes.windll.user32.IsWindow(hwnd):
                    ctypes.windll.user32.SetWindowPos(hwnd,0,b[0],b[1],b[2]-b[0],b[3]-b[1],0x0004|0x0010)
            for w in windows:
                if w['process'] in launched and w['process'] != 'Notepad.exe' and ctypes.windll.user32.IsWindow(w['hwnd']):
                    ctypes.windll.user32.PostMessageW(w['hwnd'],0x0010,0,0)
            manager.stop()
    return result


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(main(),ensure_ascii=False,indent=2))
