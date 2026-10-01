"""Explicit real desktop acceptance: launches/focuses apps and moves their windows.

No microphone, messages, terminal commands, or model calls. JSON stays in data/.
"""
from pathlib import Path
import ctypes
import json
import logging
from time import perf_counter
from kk.config import Config
from kk.runtime import build_runtime
from kk.tools.windows.native import list_monitors, foreground_window
from kk.tools.windows.windows import list_visible_windows, focus_window_handle


def main():
    config=Config.load(Path('config.toml'))
    runtime=build_runtime(config, logging.getLogger('desktop-check'), allow_offline=True)
    monitors=list_monitors()
    before={w['hwnd']:{**w, 'maximized': bool(ctypes.windll.user32.IsZoomed(w['hwnd']))} for w in list_visible_windows()}
    focused=foreground_window().get('hwnd')
    report={'monitors':monitors, 'apps':{}, 'compound':{}, 'search':{}}
    if len(monitors)<2: raise RuntimeError('This acceptance requires two real monitors')
    try:
        for app in ['PowerShell','Explorer','browser','VS Code','Codex','AyuGram','Discord','Yandex Music']:
            started=perf_counter()
            resolved=runtime.tools.app_resolver.inspect(app)
            if not resolved['found']:
                report['apps'][app]={'installed':False,'error':resolved.get('error')}
                print(app, json.dumps(report['apps'][app],ensure_ascii=False),flush=True)
                continue
            result=runtime.tools.execute('open_application',{'name':app}).as_dict()
            moves=[]
            if result['ok']:
                for index in (1,2):
                    moves.append(runtime.tools.execute('move_window_handle_to_monitor',
                        {'hwnd':result['data']['hwnd'],'monitor_index':index}).as_dict())
            report['apps'][app]={'launch':result, 'moves':moves,'seconds':perf_counter()-started}
            print(app, 'launch=',result['ok'],'monitors=',[m['ok'] for m in moves],flush=True)
        request='Открой PowerShell на втором экране, браузер на первом и Codex на втором.'
        answer=runtime.agent.ask(request)
        task=runtime.agent.orchestrator.last_task
        report['compound']={'answer':answer,'status':task.status.value,'actions':task.actions,'metrics':task.metrics}
        print('Compound:',answer,flush=True)
        answer=runtime.agent.ask('Перенеси его на второй экран.')
        report['pronoun']={'answer':answer,'status':runtime.agent.orchestrator.last_task.status.value}
        print('Pronoun:',answer,flush=True)
        # This typed query is explicitly part of the requested regression case.
        report['search']=runtime.tools.execute('search_and_type_in_app',
            {'app':'Yandex Music','text':'Ojobudo','submit':False}).as_dict()
        print('Search:',json.dumps(report['search'],ensure_ascii=False),flush=True)
        opened=runtime.agent.ask('Открой Яндекс Музыку.')
        answer=runtime.agent.ask('Открой поиск и напиши Ojobudo')
        report['search_context']={'open_answer':opened, 'answer':answer,
            'status':runtime.agent.orchestrator.last_task.status.value,
            'actions':runtime.agent.orchestrator.last_task.actions}
        print('Search context:',answer,flush=True)
    finally:
        # Restore placement of windows that already existed before the test.
        for window in list_visible_windows():
            original=before.get(window['hwnd'])
            if original:
                l,t,r,b=original['bounds']
                ctypes.windll.user32.SetWindowPos(window['hwnd'],0,l,t,r-l,b-t,0x14)
                if original['maximized']: ctypes.windll.user32.ShowWindow(window['hwnd'],3)
                elif original['minimized']: ctypes.windll.user32.ShowWindow(window['hwnd'],6)
        if focused:
            try: focus_window_handle(focused)
            except (OSError,LookupError): pass
        output=config.root/'data/desktop_reliability.json'
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        runtime.memory.close()
    failed=[]
    for name,row in report['apps'].items():
        if row.get('installed') is False and name in {'Discord','Yandex Music'}: continue
        if not row.get('launch',{}).get('ok') or len(row.get('moves',[])) != 2 or not all(m['ok'] for m in row.get('moves',[])):
            failed.append(name)
    for key in ('compound','pronoun','search_context'):
        if report.get(key,{}).get('status') != 'COMPLETED': failed.append(key)
    if not report['search'].get('ok'): failed.append('search')
    if failed: raise SystemExit('Failed: '+', '.join(failed))
    return report


if __name__=='__main__':
    main()
