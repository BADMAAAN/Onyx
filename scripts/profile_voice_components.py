"""Repeatable component measurements. Fixture audio is NOT live acceptance."""
import argparse
import json
from pathlib import Path
from time import perf_counter
import logging
import numpy as np
import sounddevice as sd
from kk.config import Config
from kk.runtime import build_runtime
from kk.voice.factory import build_voice_services
from kk.voice.audio_devices import find_device


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--label',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    config=Config.load(root/'config.toml')
    runtime=build_runtime(config,logging.getLogger('profile'))
    voice=build_voice_services(config,runtime)
    result={'source':'synthetic fixture/component benchmark; NOT live speech', 'label':args.label}
    try:
        voice.stt._load()
        start=perf_counter(); voice.tts.synthesize('Привет. Я слушаю.')
        result['tts_first_synthesis_seconds']=perf_counter()-start
        for name,text in [('greeting','Привет. Я слушаю.'),('confirmation','Блокнот открыт.')]:
            samples=[]
            for _ in range(10):
                start=perf_counter(); audio=voice.tts.synthesize(text)
                samples.append(perf_counter()-start)
            result['tts_'+name]=samples
        fixture=root/'data'/'stt_fixture.wav'
        if fixture.exists():
            audio=fixture
            if args.label == 'memory':
                from faster_whisper.audio import decode_audio
                audio=decode_audio(str(fixture),sampling_rate=16000)
            voice.stt.transcribe(audio)
            result['stt']=[]
            for _ in range(10):
                heard=voice.stt.transcribe(audio)
                result['stt'].append({'seconds':heard.latency_seconds,'text':heard.text})
        result['audio_startup']=[]
        for _ in range(10):
            start=perf_counter()
            sd.play(np.zeros(4800,dtype=np.float32),48000,device=find_device(voice.tts.output_device,input_device=False))
            result['audio_startup'].append(perf_counter()-start)
            sd.wait()
        path=root/'data'/('latency_'+args.label+'_benchmark.json')
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        for key,values in result.items():
            if isinstance(values,list):
                numbers=[v['seconds'] if isinstance(v,dict) else v for v in values]
                print(key,{'median':float(np.median(numbers)),'p90':float(np.percentile(numbers,90)),
                           'best':min(numbers),'worst':max(numbers)},flush=True)
            else: print(key,values,flush=True)
    finally:
        voice.loop.speech.close(); runtime.context.stop(); runtime.memory.close()

if __name__=='__main__': main()
