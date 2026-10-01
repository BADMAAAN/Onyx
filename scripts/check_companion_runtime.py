"""Physical endpoint smoke + replay through the full voice event pipeline.

The replay is labelled synthetic. No user commands or media words are dispatched.
"""
from pathlib import Path
import sys
import json
import logging
from threading import Thread
from time import monotonic,sleep
from unittest.mock import patch
import numpy as np
import psutil
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    from kk.config import Config
    from kk.runtime import build_runtime
    from kk.voice.factory import build_voice_services
    from kk.voice.audio_input import AudioInput
    from kk.voice.audio_devices import find_device
    from kk.core.state import AgentState
    from faster_whisper.audio import decode_audio
    from scripts.benchmark_companion_stt import resources
    config=Config.load(ROOT/'config.toml'); runtime=build_runtime(config,logging.getLogger('voice-smoke'))
    voice=build_voice_services(config,runtime)
    result={'source':'physical endpoints + synthetic WAV replay; NOT live user acceptance'}
    process=psutil.Process(); process.cpu_percent()
    try:
        with AudioInput(find_device(voice.stt.microphone,input_device=True),480) as mic:
            packets=[mic.read(480) for _ in range(10)]
        result['physical_capture']=dict(packets=len(packets),valid=sum(bool(p) and not o for p,o in packets))
        voice.stt.prewarm(); voice.tts.prewarm()
        process.cpu_percent(); sleep(2)
        baseline=resources(); baseline['cpu_percent']=process.cpu_percent(); result['idle']=baseline
        result['cpu_definition']='process percent, 100 = one logical CPU; GPU memory is total device usage including Brain/desktop'
        states=[]; runtime.state.subscribe(lambda s,d:states.append(dict(state=s.value,seconds=monotonic())))
        voice.loop.audio_focus.enabled=False  # ducking checked separately against real sessions
        audio=decode_audio(str(ROOT/'data/latency_B.wav'),sampling_rate=16000)
        pcm=np.clip(audio*32768,-32768,32767).astype(np.int16).tobytes()+bytes(16000*2*3)
        position=[0]
        class Replay:
            def __init__(self,*a,**kw): pass
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def read(self,n):
                sleep(.03)
                if not voice.loop.capture or not voice.loop.capture.enabled(): return bytes(960),False
                begin=position[0]; position[0]+=960
                return (pcm[begin:begin+960].ljust(960,b'\0') if begin<len(pcm) else bytes(960)),False
        requests=[]
        def answer(text,on_token=None,cancellation=None):
            requests.append(text)
            if on_token: on_token('Готово. Голосовая проверка завершена.')
            return 'Готово.'
        with patch('kk.voice.audio_input.AudioInput',Replay),patch.object(runtime.agent,'ask',answer):
            thread=Thread(target=voice.loop._streaming_session,daemon=True); thread.start()
            start=monotonic(); voice.loop.trigger()
            deadline=start+35
            while monotonic()<deadline and not voice.loop.speech.audio_started.is_set(): sleep(.05)
            result['requests']=requests
            result['replay_to_first_audio_seconds']=monotonic()-start if voice.loop.speech.audio_started.is_set() else None
            result['tts_first_audio_seconds']=voice.loop.speech.first_audio_seconds
            result['during_voice']=resources(); result['during_voice']['cpu_percent']=process.cpu_percent()
            result['states']=states
            result['final']=voice.loop.last_transcription.text if voice.loop.last_transcription else None
            voice.loop.stop(); thread.join(5)
            result['capture_thread_stopped']=not voice.loop.capture.thread.is_alive()
    finally:
        voice.loop.stop(); voice.loop.speech.close(); voice.loop.audio_focus.close()
        runtime.context.stop(); runtime.memory.close()
    (ROOT/'data/companion_runtime_acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
