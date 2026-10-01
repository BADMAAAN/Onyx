"""Ten warm fixture replays per command; live capture/VAD are not simulated."""
import argparse
import json
import logging
import subprocess
from pathlib import Path
from time import monotonic
from time import sleep
import numpy as np
from faster_whisper.audio import decode_audio
from kk.config import Config
from kk.runtime import build_runtime
from kk.voice.factory import build_voice_services
from kk.voice.speech_queue import SentenceBuffer


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--count',type=int,default=10)
    parser.add_argument('--commands',default='ABCD')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    config=Config.load(root/'config.toml')
    runtime=build_runtime(config,logging.getLogger('command-benchmark'))
    voice=build_voice_services(config,runtime)
    results={'source':'synthetic audio replay, real tools/playback; no live endpoint measurement','trials':[]}
    path=root/'data'/('latency_commands_'+args.commands+'_benchmark.json')
    commands={'A':'Кей Кей, привет','B':'Кей Кей, открой блокнот',
              'C':'Кей Кей, какая громкость?','D':'Кей Кей, что написано в этом окне?'}
    try:
        voice.stt.prewarm(); voice.tts.prewarm()
        for label,text in commands.items():
            if label not in args.commands:
                continue
            fixture=root/'data'/('latency_'+label+'.wav')
            voice.tts.save_wav(text,fixture)
            audio=decode_audio(str(fixture),sampling_rate=16000)
            if label=='D':
                document=root/'data'/'perception_fixture.txt'
                document.write_text('СИНИЙ МАЯК 7429\nSave Bluetooth settings\nKK работает',encoding='utf-8')
                subprocess.Popen(['notepad.exe',str(document)])
                sleep(1)
                focused=runtime.tools.execute('focus_window',{'title':'perception_fixture.txt'})
                if not focused.ok:
                    raise RuntimeError('D fixture focus failed: '+str(focused.error))
            for index in range(args.count):
                started=monotonic()
                heard=voice.stt.transcribe(audio)
                ready=monotonic()
                token=voice.loop.speech.begin()
                buffer=SentenceBuffer(lambda c:voice.loop.speech.submit(c,token))
                answer=runtime.agent.ask(heard.text,on_token=buffer.feed,cancellation=token)
                buffer.flush(); voice.loop.speech.finish(token); voice.loop.speech.wait(token)
                task=runtime.agent.orchestrator.last_task
                row={'command':label,'repeat':index+1,'expected':text,'transcription':heard.text,
                     'answer':answer,'stt_seconds':heard.latency_seconds,'metrics':task.metrics,
                     'status':task.status.value,'tools':[a['tool'] for a in task.actions],
                     'tool_execution_seconds':[a.get('execution_seconds') for a in task.actions],
                     'verification_seconds':[a.get('verification_seconds') for a in task.actions],
                     'post_stt_first_audio_seconds':voice.loop.speech.first_audio_seconds,
                     'replay_to_first_audio_seconds':ready-started+voice.loop.speech.first_audio_seconds,
                     'replay_total_seconds':monotonic()-started,'trace':token.trace}
                events=token.trace
                first=lambda name:next((e['monotonic'] for e in events if e['event']==name),None)
                decision,action=first('intent_decided'),first('tool_start')
                row['stt_to_decision_seconds']=decision-ready if decision else None
                row['decision_to_action_seconds']=action-decision if action and decision else None
                row['replay_to_action_seconds']=action-started if action else None
                results['trials'].append(row)
                path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
                print(label,index+1,heard.text,row['status'],round(row['replay_to_first_audio_seconds'],3),flush=True)
        summary={}
        for label in commands:
            rows=[r for r in results['trials'] if r['command']==label]
            summary[label]={}
            for metric in ('stt_seconds','stt_to_decision_seconds','decision_to_action_seconds',
                           'replay_to_action_seconds','post_stt_first_audio_seconds','replay_to_first_audio_seconds','replay_total_seconds'):
                numbers=[r[metric] for r in rows if r.get(metric) is not None]
                if numbers: summary[label][metric]={'median':float(np.median(numbers)),
                    'p90':float(np.percentile(numbers,90)),'best':min(numbers),'worst':max(numbers)}
        results['summary']=summary
        path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    finally:
        voice.loop.speech.close(); runtime.context.stop(); runtime.memory.close()

if __name__=='__main__': main()
