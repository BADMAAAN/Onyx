"""Serial new-symptom acceptance through production text or audio handlers.

Run only with background Onyx stopped. --prepare writes synthetic SAPI fixtures;
--mode text/replay uses one SingleInstance and never opens a browser or overlay.
Audio replay is not evidence of recognition quality on a human microphone.
"""
from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.replay_diagnostics import ReplayProbe, assess_replay, sha256, wav_evidence
FOLDER=ROOT/'data/voice_diagnostics/real_speech'
CASES=[
    {'id':'music','text':'Открой Яндекс Музыку'},
    {'id':'close_code','text':'VS Code закрой'},
    {'id':'obsidian','text':'открой мне Обсидиан'},
    {'id':'folder','text':'открой папку Onyx на рабочем столе'},
    {'id':'negation','text':'не закрывай VS Code, открой папку Onyx на рабочем столе'},
    {'id':'literal','text':'открой Обсидиан и напиши: Onyx, мне нужен ifconfig'},
    {'id':'repeat','text':'Повтори слово YouTube'},
]


def wait_for(predicate, seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.025)
    raise TimeoutError('Controlled acceptance condition timed out')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--mode',choices=['text','replay'],default='text')
    parser.add_argument('--case',action='append',choices=[c['id'] for c in CASES])
    parser.add_argument('--label',default='current')
    args=parser.parse_args()
    if not args.label.replace('_','').isalnum(): parser.error('label must be alphanumeric')
    FOLDER.mkdir(parents=True,exist_ok=True)
    selected=[case for case in CASES if not args.case or case['id'] in args.case]
    if args.prepare:
        manifest_path=FOLDER/'replay_manifest_v2.json'
        if manifest_path.exists(): parser.error('v2 manifest already exists; fixtures are immutable')
        manifest=[dict(case, source_text='Оникс, '+case['text'],
            source_text_sha256=sha256(('Оникс, '+case['text']).encode('utf-8')),
            wav_name=f"input_v2_{case['id']}.wav", synthetic=True,
            synthesis='SAPI Microsoft Irina Desktop; single voice; no engine switches; no audio splicing') for case in CASES]
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        subprocess.run(['powershell.exe','-NoProfile','-File',str(ROOT/'scripts/prepare_real_speech.ps1')],check=True)
        from kk.config import Config
        config=Config.load(ROOT/'config.toml')
        for case in manifest:
            case['audio']=wav_evidence(FOLDER/case['wav_name'],config.get('voice.silence_threshold',.0015),
                                       config.get('voice.silence_seconds',.75))
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'manifest':str(manifest_path),'cases':[{ 'id':c['id'],
            'duration_seconds':c['audio']['duration_seconds'],
            'internal_endpoint_sized_silences':c['audio']['internal_endpoint_sized_silences']} for c in manifest]},ensure_ascii=False),flush=True)
        return
    from kk.config import Config
    from kk.core.instance import SingleInstance
    from kk.core.task import CancellationToken
    from kk.logging_setup import configure_logging
    from kk.runtime import build_runtime
    report={'mode':args.mode,'rows':[],'source':'production Orchestrator' if args.mode=='text' else
        'controlled single-voice SAPI WAV -> AudioInput -> production VAD/STT/wake/VoiceLoop/TTS/PortAudio',
        'human_voice_quality':'UNVERIFIED', 'browser_live':'not exercised'}
    output=FOLDER/f'{args.mode}_{args.label}.json'
    if output.exists(): parser.error('Report already exists; use a new label to preserve failures')
    manifest={}
    if args.mode=='replay':
        preflight_config=Config.load(ROOT/'config.toml')
        manifest={c['id']:c for c in json.loads((FOLDER/'replay_manifest_v2.json').read_text(encoding='utf-8'))}
        # The original mixed-voice files and final failure report are retained.
        # A known artificial endpoint-sized gap must not become a live tail action.
        for case in selected:
            fixture=manifest[case['id']]
            evidence=wav_evidence(FOLDER/fixture['wav_name'],preflight_config.get('voice.silence_threshold',.0015),
                                  preflight_config.get('voice.silence_seconds',.75))
            if evidence['wav_sha256'] != fixture['audio']['wav_sha256']:
                parser.error('Fixture hash changed: '+case['id'])
            if sha256(fixture['source_text'].encode('utf-8')) != fixture['source_text_sha256']:
                parser.error('Fixture source text hash changed: '+case['id'])
            if evidence['internal_endpoint_sized_silences']:
                parser.error('Synthetic fixture has endpoint-sized internal silence: '+case['id'])
    def save(): output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    instance=SingleInstance(ROOT/'data/kk.lock')
    runtime=voice=voice_thread=replay_context=None
    try:
        config=Config.load(ROOT/'config.toml')
        from kk.core.runtime_identity import startup_identity
        report['runtime_identity']=startup_identity(ROOT, ROOT/'config.toml')
        logger=configure_logging(ROOT/'logs')
        logger.handlers[0].setLevel(logging.WARNING)
        runtime=build_runtime(config,logger)
        runtime.policy.clear_stop()
        if args.mode=='replay':
            from kk.voice.factory import build_voice_services
            from scripts.repair_replay import replay_audio_input
            voice=build_voice_services(config,runtime)
            voice.loop.diagnostic=True
            voice.loop.wakeword=True
            replay_context=replay_audio_input(source='controlled_new_symptom_sapi_replay')
            source=replay_context.__enter__()
            voice_thread=threading.Thread(target=voice.loop.run,name='NewSymptomVoiceRuntime',daemon=True)
            voice_thread.start()
            wait_for(lambda:voice.loop.capture and voice.loop.capture.ready.is_set(),90)
            probe=ReplayProbe(voice.loop)
            report['tts']=voice.tts.runtime_diagnostics()
        for case in selected:
            started=time.monotonic()
            row={'case':case['id'],'input':case['text']}
            try:
                if args.mode=='text':
                    token=CancellationToken(diagnostic=True)
                    chunks=[]
                    answer=runtime.agent.ask(case['text'],on_token=chunks.append,cancellation=token)
                    task=runtime.agent.orchestrator.last_task
                    assert task and task.id==token.generation_id, 'Mismatched task result'
                    row.update(task_id=task.id,final_transcript=task.final_transcript,answer=answer,
                        status=task.status.value,metrics=task.metrics,actions=task.actions,trace=token.trace,
                        callback=''.join(chunks),callback_complete=''.join(chunks)==answer)
                else:
                    fixture=manifest[case['id']]
                    row['fixture']=fixture
                    probe.begin_case(case['id'])
                    row['pcm']=source.feed_wav(FOLDER/fixture['wav_name'],label=fixture['source_text'])
                    source.wait_empty(35)
                    # Wait for actual work, including every emitted event. A rejected
                    # no-wake final may deliberately have no speech token at all.
                    stable_since=None
                    def settled():
                        nonlocal stable_since
                        if probe.settled():
                            if stable_since is None: stable_since=time.monotonic()
                            return time.monotonic()-stable_since >= .75
                        stable_since=None
                        return False
                    wait_for(settled,180)
                    observation=probe.snapshot()
                    row.update(observation)
                    if len(observation['command_results'])==1:
                        snapshot=observation['command_results'][0]
                        row.update(snapshot)
                        trace=snapshot.get('trace',[])
                        generated={e['chunk_id']:e['generated_frames'] for e in trace if e['event']=='tts_synthesis_end'}
                        completed={e['chunk_id']:e['completed_frames'] for e in trace if e['event']=='audio_playback_complete'}
                        row['all_audio_drained']=bool(generated) and generated==completed
                        row['speech_text']=[e.get('speech_text') for e in trace if e['event']=='speech_ready']
                        row['spoken_text']=voice.loop.speech.spoken_text.strip()
                        row['spoken_answer_complete']=' '.join(row['spoken_text'].split())==' '.join((row.get('answer') or '').split())
                    row.update(assess_replay(case['text'],observation))
                    if row['acceptance_status']=='PASS' and not (row.get('all_audio_drained') and row.get('spoken_answer_complete')):
                        row.update(acceptance_status='UNVERIFIED',reason='Complete spoken answer and PCM drain not proven')
                    row['status']=row['acceptance_status']
                row['seconds']=time.monotonic()-started
            except Exception:
                row.update(status='UNVERIFIED',error=traceback.format_exc(),seconds=time.monotonic()-started)
                if voice:row.update(probe.snapshot())
                if voice and voice.loop.generation and not voice.loop.generation.completed.is_set():
                    voice.loop.interrupt(reason='acceptance_timeout',source='controlled_replay')
            report['rows'].append(row)
            save()
            print(json.dumps({k:row.get(k) for k in ('case','status','final_transcript','answer','reason','error')},ensure_ascii=False),flush=True)
            if voice:time.sleep(.5)
    except Exception:
        report['fatal']=traceback.format_exc()
        raise
    finally:
        if voice:
            voice.loop.stop()
            voice.loop.speech.close()
            if voice_thread:voice_thread.join(8)
            if voice.loop.audio_focus:voice.loop.audio_focus.close()
        if replay_context:replay_context.__exit__(None,None,None)
        if runtime:
            runtime.context.stop()
            runtime.provider.resource_manager.idle_stop.set()
            runtime.memory.close()
        instance.close()
        report['shutdown']='complete'
        save()


if __name__=='__main__':main()
