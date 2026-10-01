"""Opt-in real desktop smoke checks; no microphone, no scripted speech acceptance."""
import argparse
import json
import logging
from pathlib import Path
import sys
from time import perf_counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('case',choices=['windows','firefox','music'])
    args=parser.parse_args()
    from kk.config import Config
    from kk.runtime import build_runtime
    from kk.tools.windows.window_control import WindowController
    logging.basicConfig(level=logging.WARNING)
    runtime=build_runtime(Config.load(ROOT/'config.toml'),logging.getLogger('check'),allow_offline=True)
    requests={'windows':['Открой TG','Сделай его на полный экран','Перенеси на второй','Теперь открой Discord'],
        'firefox':['Открой Firefox, зайди на YouTube и найди видео про чёрные дыры'],
        'music':['Открой Яндекс Музыку и включи последний добавленный трек']}[args.case]
    results=[]
    try:
        target=None
        for request in requests:
            print('REQUEST: '+request,flush=True); started=perf_counter()
            try: answer=runtime.agent.ask(request)
            except Exception as exc: answer=type(exc).__name__+': '+str(exc)
            task=runtime.agent.orchestrator.last_task
            row={'request':request,'answer':answer,'seconds':perf_counter()-started,
                'status':str(task.status) if task else None,
                'actions':[{'tool':a['tool'],'arguments':a['arguments'],'ok':a.get('result',{}).get('ok'),
                    'seconds':a.get('execution_seconds'),'error':a.get('result',{}).get('error')} for a in task.actions] if task else []}
            if args.case=='windows':
                context=runtime.tools.desktop_context
                current=WindowController(runtime.tools).state(context.hwnd)
                row['target']={k:current.get(k) for k in ['hwnd','process','process_id','bounds','monitor_index','maximized','minimized']}
                if target is None: target=context.hwnd
                if request=='Сделай его на полный экран': assert current['hwnd']==target and current['maximized']
                if request=='Перенеси на второй': assert current['hwnd']==target and current['monitor_index']==2
                if request=='Теперь открой Discord': assert current['hwnd']!=target
            results.append(row); print(json.dumps(row,ensure_ascii=False),flush=True)
    finally:
        (ROOT/'data'/f'polish_{args.case}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        runtime.context.stop(); runtime.memory.close()

if __name__=='__main__': main()
