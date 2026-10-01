"""Real on-screen semantic vision fixture, separate from microphone latency."""
import json
import logging
from pathlib import Path
import subprocess
import sys
from time import perf_counter,sleep
from kk.config import Config
from kk.runtime import build_runtime


def main():
    root=Path(__file__).resolve().parents[1]
    process=subprocess.Popen([sys.executable,str(root/'scripts/vision_fixture_window.py')],cwd=root)
    runtime=build_runtime(Config.load(root/'config.toml'),logging.getLogger('visual-benchmark'))
    trials=[]
    try:
        sleep(1)
        for index in range(10):
            focus=runtime.tools.execute('focus_window',{'title':'KK vision acceptance fixture'})
            if not focus.ok: raise RuntimeError(focus.error)
            started=perf_counter()
            result=runtime.tools.execute('see_screen',{'question':'Что изображено на экране?','mode':'vision'})
            description=(result.data or {}).get('description','') if result.ok else ''
            row={'repeat':index+1,'seconds':perf_counter()-started,'result':result.as_dict(),
                 'quality_pass':'красн' in description.casefold() and 'круг' in description.casefold()
                     and 'син' in description.casefold() and 'квадрат' in description.casefold()}
            trials.append(row)
            (root/'data/latency_visual_benchmark.json').write_text(json.dumps(trials,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(index+1,round(row['seconds'],3),result.ok,row['quality_pass'],flush=True)
    finally:
        process.terminate(); process.wait(timeout=5)
        runtime.context.stop(); runtime.memory.close()

if __name__=='__main__': main()
