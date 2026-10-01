"""Interactive physical microphone trace. Never supplies expected text to STT."""
import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys
import types
from time import monotonic, perf_counter
from kk.config import Config
from kk.runtime import build_runtime
from kk.voice.factory import build_voice_services
from kk.voice.speech_queue import SentenceBuffer


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    config=Config.load(root/'config.toml')
    runtime=build_runtime(config,logging.getLogger('live-trace'))
    voice=build_voice_services(config,runtime)
    if args.baseline:
        source=subprocess.check_output(['git','show','v0.3-realtime-companion:kk/voice/stt_faster_whisper.py'],cwd=root).decode('utf-8')
        module=types.ModuleType('kk.voice._baseline_stt'); sys.modules[module.__name__]=module
        exec(compile(source,'baseline_stt','exec'),module.__dict__)
        voice.stt.__class__=module.FasterWhisperSTT
    trials=[]
    path=root/'data'/('latency_live_'+('baseline' if args.baseline else 'optimized')+'_benchmark.json')
    try:
        voice.stt._load()
        for _ in range(3): voice.tts.synthesize('Привет. Я слушаю.')
        for command in ['Кей Кей, привет','Кей Кей, открой блокнот']:
            input('Enter: после сигнала произнесите '+command+'\n')
            events=[]; stages={}
            def event(kind,data):
                if kind!='level':
                    events.append({'event':kind,'monotonic':monotonic(),**data})
                    print(kind,data,flush=True)
            heard=voice.stt.listen(on_event=event,ready_beep=True,calibration_seconds=1.5)
            stt_ready=monotonic()
            print('TRANSCRIPTION:',heard.text,'STT:',heard.latency_seconds,flush=True)
            if not heard.text:
                trials.append({'expected':command,'transcription':'','events':events,'error':'empty STT'})
                path.write_text(json.dumps(trials,ensure_ascii=False,indent=2),encoding='utf-8')
                continue
            synth=voice.tts.synthesize
            def timed_synth(text):
                stages.setdefault('tts_synthesis_start_monotonic',monotonic())
                audio=synth(text)
                stages.setdefault('tts_synthesis_end_monotonic',monotonic())
                return audio
            voice.tts.synthesize=timed_synth
            token=voice.loop.speech.begin()
            playback_origin=monotonic()
            def submit(chunk):
                stages.setdefault('tts_enqueue_monotonic',monotonic())
                voice.loop.speech.submit(chunk,token)
            buffer=SentenceBuffer(submit)
            decision_start=monotonic()
            answer=runtime.agent.ask(heard.text,on_token=buffer.feed,cancellation=token)
            buffer.flush(); voice.loop.speech.finish(token); voice.loop.speech.wait(token)
            voice.tts.synthesize=synth
            task=runtime.agent.orchestrator.last_task
            endpoint=next(e for e in events if e['event']=='speech_end')
            end_estimate=endpoint['monotonic']-endpoint['end_of_speech_detection_seconds']
            first_audio=playback_origin+voice.loop.speech.first_audio_seconds
            trial={'expected':command,'transcription':heard.text,'answer':answer,'events':events,
                   'source':'physical Focusrite','endpoint_basis':'last VAD-positive frame estimate, not acoustic annotation',
                   'first_audio_basis':'PortAudio submission, not measured acoustic arrival',
                   'speech_end_to_stt_seconds':stt_ready-end_estimate,
                   'stt_seconds':heard.latency_seconds,'vad_trailing_seconds':endpoint['end_of_speech_detection_seconds'],
                   'speech_end_to_first_audio_seconds':first_audio-end_estimate,
                   'speech_end_to_completion_seconds':monotonic()-end_estimate,
                   'metrics':task.metrics,'actions':task.actions,'stages':stages,
                   'trace':token.trace,
                   'decision_start_monotonic':decision_start}
            trials.append(trial)
            path.write_text(json.dumps(trials,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(json.dumps({k:v for k,v in trial.items() if k not in {'events','actions'}},ensure_ascii=False,indent=2),flush=True)
    finally:
        voice.loop.speech.close(); runtime.context.stop(); runtime.memory.close()

if __name__=='__main__': main()
