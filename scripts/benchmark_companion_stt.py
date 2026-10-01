"""Real model/hardware timings. Fixture results are NEVER labelled live accuracy."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread, Event
from time import perf_counter
from urllib.request import urlopen, Request
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def distance(a,b):
    row = list(range(len(b)+1))
    for i,x in enumerate(a,1):
        new=[i]
        for j,y in enumerate(b,1): new.append(min(new[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=new
    return row[-1]

def errors(expected, actual):
    normalize=lambda s: ' '.join(re.findall(r'\w+',s.casefold()))
    expected,actual=normalize(expected),normalize(actual)
    return dict(word_errors=distance(expected.split(),actual.split()), reference_words=len(expected.split()),
                char_errors=distance(expected,actual), chars=len(expected))

def api(path, data=None):
    request=Request('http://127.0.0.1:11434/api/'+path,
        data=json.dumps(data).encode() if data else None, headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=180) as response: return json.load(response)

def resources():
    import psutil
    result=dict(rss_mb=psutil.Process().memory_info().rss/1048576)
    try:
        result['gpu']=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu',
            '--format=csv,noheader,nounits'],creationflags=0x08000000,text=True,timeout=5).strip()
    except Exception as exc: result['gpu_error']=str(exc)
    return result

def child(args):
    from kk.config import Config
    from kk.voice.stt_faster_whisper import FasterWhisperSTT
    config=Config.load(ROOT/'config.toml')
    result=dict(model=args.model,device=args.device,compute=args.compute,
                source='synthetic Silero fixture replay, NOT live Focusrite',trials=[],samples=[])
    path=ROOT/'data'/f'stt_{args.model}_{args.device}_{args.compute}_benchmark.json'
    quit=Event()
    def sample():
        while not quit.is_set():
            result['samples'].append(resources()); quit.wait(.5)
    monitor=Thread(target=sample,daemon=True); monitor.start()
    try:
        result['brain_before']=api('ps')
        stt=FasterWhisperSTT(args.model,args.device,args.compute,'auto',download_root=ROOT/'data/models/faster-whisper')
        stt.cpu_threads=6
        stt.hotwords=config.get('voice.hotwords',[])
        started=perf_counter(); stt.prewarm(); result['load_warm_seconds']=perf_counter()-started
        commands={'A':'Кей Кей, привет','B':'Кей Кей, открой блокнот','C':'Кей Кей, какая громкость?',
                  'D':'Кей Кей, что написано в этом окне?'}
        for label,expected in commands.items():
            for repeat in range(2):
                heard=stt.transcribe(ROOT/'data'/f'latency_{label}.wav')
                result['trials'].append(dict(expected=expected,repeat=repeat,**asdict(heard),**errors(expected,heard.text)))
        silence=stt.transcribe(np.zeros(16000*120,dtype=np.float32))
        result['digital_silence_120s']=asdict(silence)
        started=perf_counter()
        brain=api('generate',{'model':'qwen2.5:14b','prompt':'Ответь одним словом: готов?',
            'stream':False,'keep_alive':'30m','options':{'num_ctx':16384,'num_predict':4}})
        result['brain_after_seconds']=perf_counter()-started
        result['brain_load_seconds']=brain.get('load_duration',0)/1e9
        result['brain_after']=api('ps')
    except Exception as exc: result['error']=f'{type(exc).__name__}: {exc}'
    finally:
        quit.set(); monitor.join(6)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('samples','trials','brain_before','brain_after')},ensure_ascii=False),flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model'); parser.add_argument('--device'); parser.add_argument('--compute')
    args=parser.parse_args()
    if args.model: return child(args)
    api('generate',{'model':'qwen2.5:14b','prompt':'Готов?', 'stream':False,'keep_alive':'30m',
                    'options':{'num_ctx':16384,'num_predict':2}})
    outcomes=[]
    for model in ('small','large-v3-turbo'):
        for device,compute in [('cpu','int8'),('cpu','float32'),('cpu','float16'),
                               ('cuda','float16'),('cuda','int8_float16'),('cuda','int8')]:
            command=[sys.executable,str(Path(__file__).resolve()),'--model',model,'--device',device,'--compute',compute]
            try:
                process=subprocess.run(command,cwd=ROOT,env={**os.environ,'PYTHONUTF8':'1'},
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',timeout=360,creationflags=0x08000000)
                outcomes.append(dict(model=model,device=device,compute=compute,exit_code=process.returncode,output=process.stdout[-6000:]))
            except subprocess.TimeoutExpired:
                outcomes.append(dict(model=model,device=device,compute=compute,error='timeout_360s'))
            print(json.dumps(outcomes[-1],ensure_ascii=False),flush=True)
            (ROOT/'data/stt_matrix_benchmark.json').write_text(json.dumps(outcomes,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': main()
