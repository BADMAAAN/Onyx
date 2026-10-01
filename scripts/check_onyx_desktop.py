"""Run requested reversible desktop acceptance cases through the production router."""
import json
import logging
from pathlib import Path
import sys
from time import perf_counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kk.config import Config
from kk.runtime import build_runtime
from kk.core.fast_commands import run_fast
from kk.core.task import AgentTask


def main():
    runtime=build_runtime(Config.load(ROOT/'config.toml'),logging.getLogger('onyx-acceptance'),allow_offline=True)
    rows=[]
    try:
        for command in ['Оникс, открой Telegram.', 'Открой TG на full.', 'Перенеси на второй.',
                        'Открой Firefox, открой YouTube.', 'Что на первом экране?', 'Что на втором экране?',
                        'Открой Яндекс Музыку.', 'Включи музыку.', 'Открой DS на full.',
                        'Открой браузер и GPT.', 'Открой настройки.', 'Bluetooth.']:
            task=AgentTask(command)
            token=runtime.tools.cancellation.set(task.cancellation_token)
            started=perf_counter()
            try:
                answer=run_fast(command,task,runtime.tools,runtime.state)
                rows.append(dict(command=command,answer=answer,status=task.status.value,seconds=perf_counter()-started,
                    needs_planner=answer is None,actions=[{'tool':a['tool'],'ok':a.get('result',{}).get('ok'),
                    'verified':(a.get('result',{}).get('data') or {}).get('verified') if isinstance(a.get('result',{}).get('data'),dict) else None,
                    'error':a.get('result',{}).get('error')} for a in task.actions]))
                print(json.dumps(rows[-1],ensure_ascii=False),flush=True)
            finally: runtime.tools.cancellation.reset(token)
    finally:
        output=ROOT/'data/onyx_benchmark';output.mkdir(exist_ok=True)
        (output/'desktop.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        runtime.memory.close()

if __name__=='__main__': main()
