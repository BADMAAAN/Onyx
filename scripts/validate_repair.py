"""Opt-in local repair acceptance. Uses the production Orchestrator/VoiceLoop.

Run serially with background Onyx stopped. Captured test text/audio stays under
ignored data/voice_diagnostics/repair. Never a substitute for human acoustics.
"""
from __future__ import annotations
import argparse, json, logging, time, traceback, threading, uuid
from pathlib import Path
import psutil
from kk.config import Config
from kk.core.instance import SingleInstance
from kk.core.task import CancellationToken
from kk.logging_setup import configure_logging
from kk.runtime import build_runtime

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
 'Из чего делают чай?',
 'А чем зелёный чай отличается от чёрного?',
 'Расскажи про ChatGPT простыми словами',
 'А почему он иногда ошибается?',
 'Объясни, чем оперативная память отличается от видеопамяти',
 'А почему выключение компьютера стирает данные из оперативной памяти?',
 'Почему небо кажется голубым?',
 'Как получается радуга?',
 'Что такое Firefox?',
 'Расскажи, почему лёд плавает в воде',
]
DESKTOP = [
 'Открой TG', 'Открой DS', 'Открой Firefox, открой YouTube',
 'Разверни его', 'Перенеси браузер на второй',
 'Открой Яндекс Музыку', 'Включи музыку',
 'Перенеси Firefox на первый экран',
 'В Блокноте напиши: Я открыла Visual Studio Code',
 'Открой PowerShell и напиши ifconfig',
 'Что на первом экране?', 'Что на втором?',
]
FIXED = ('Первое предложение проверяет начало озвучки. '
         'Второе предложение проверяет порядок и паузы. '
         'Последнее предложение заканчивается словами: контрольный хвост дошёл')


def wait_for(predicate, seconds=180):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(.02)
    raise TimeoutError('Acceptance condition timed out')


def validate_report(report):
    issues=[]
    if not report['rows']:issues.append('no turns verified')
    for index,row in enumerate(report['rows']):
        if row.get('error'):issues.append(f'turn {index+1}: exception')
        if 'status' in row and row['status']!='COMPLETED':issues.append(f'turn {index+1}: {row["status"]}')
        trace=row.get('speech_trace',row.get('trace',[]))
        generated={e['chunk_id']:e['generated_frames'] for e in trace if e['event']=='tts_synthesis_end'}
        completed={e['chunk_id']:e['completed_frames'] for e in trace if e['event']=='audio_playback_complete'}
        submitted={e['chunk_id']:e['submitted_frames'] for e in trace if e['event']=='audio_submitted'}
        if report['mode'] in {'speech','duplex','fixed','replay','replay_interrupt'}:
            if not generated or generated!=submitted or generated!=completed:issues.append(f'turn {index+1}: audio tail/frame mismatch')
            order=[e['chunk_id'] for e in trace if e['event']=='audio_device_start']
            if order!=list(generated) or list(completed)!=list(generated):issues.append(f'turn {index+1}: audio order mismatch')
            if any(e.get('underflows',0) for e in trace):issues.append(f'turn {index+1}: output underflow')
            if not any(e['event']=='speech_complete' for e in trace):issues.append(f'turn {index+1}: no playback drain')
            expected=row.get('answer') if 'answer' in row else row.get('input','')
            if ' '.join((expected or '').split())!=' '.join(row.get('spoken_text','').split()):issues.append(f'turn {index+1}: spoken text mismatch')
        if report['mode']=='text' and row.get('callback')!=row.get('answer'):issues.append(f'turn {index+1}: response callback mismatch')
    if report['mode']=='interrupt' and report['rows']:
        row=report['rows'][0]
        if not row.get('candidate_kept_playing') or row.get('stale_audio'):issues.append('interruption invariant failed')
    if report['mode']=='replay_interrupt':
        check=report.get('interruption',{})
        if not check.get('old_speech_cancelled') or check.get('old_speech_cancel_reason')!='confirmed_barge_in':issues.append('old speech not cancelled by recognized input')
        if not check.get('old_generation_cancelled') and not check.get('old_generation_completed'):issues.append('superseded computation still active')
        if check.get('stale_audio_after_new_turn') or check.get('stale_audio_after_cancel'):issues.append('superseded reply returned')
    if report.get('fatal'):issues.append('fatal runner error')
    return dict(passed=not issues,issues=issues,turns=len(report['rows']),
                basis=('production Orchestrator and native desktop evidence' if report['mode'] in {'text','desktop'} else 'production handlers and PortAudio output drain; no claim of acoustic measurement'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['text','speech','fixed','duplex','desktop','replay','replay_interrupt','interrupt'])
    p.add_argument('--utterance',action='append',help='Explicit diagnostic utterances, in order')
    p.add_argument('--label',default='',help='Alphanumeric output label')
    args=p.parse_args()
    if args.label and not args.label.replace('_','').isalnum():p.error('label must be alphanumeric')
    folder=ROOT/'data/voice_diagnostics/repair';folder.mkdir(parents=True,exist_ok=True)
    output=folder/(args.mode+('_'+args.label if args.label else '')+'.json')
    config=Config.load(ROOT/'config.toml')
    logger=configure_logging(ROOT/'logs')
    logger.handlers[0].setLevel(logging.WARNING)
    instance=SingleInstance(ROOT/'data/kk.lock')
    runtime=build_runtime(config,logger);runtime.policy.clear_stop()
    voice=overlay=voice_thread=replay_context=replay_source=None
    rows=[];resource_rows=[];sampling_stop=threading.Event()
    report=dict(mode=args.mode,source='text via production Orchestrator',rows=rows,resources=resource_rows)
    def save():
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    def sample():
        while not sampling_stop.is_set():
            try:
                row=runtime.provider.resource_manager.snapshot();row['at']=time.monotonic()
                resource_rows.append(row)
            except Exception as exc: resource_rows.append(dict(error=str(exc)))
            sampling_stop.wait(1)
    sampler=threading.Thread(target=sample,daemon=True);sampler.start()
    def row_for(question,token,started):
        task=runtime.agent.orchestrator.last_task
        row=dict(input=question,seconds=time.perf_counter()-started,trace=token.trace,
                 answer=task.result if task else None,status=task.status.value if task else None,
                 metrics=task.metrics if task else {},actions=task.actions if task else [])
        speech=getattr(token,'speech_token',None)
        if speech:
            row.update(speech_trace=speech.trace,cancel_reason=speech.cancel_reason,
                       spoken_text=voice.loop.speech.spoken_text,
                       first_audio_seconds=voice.loop.speech.first_audio_seconds)
        return row
    try:
        if args.mode in {'text','desktop'}:
            if args.mode=='desktop':runtime.context.sample();runtime.context.start()
            for question in (args.utterance or (QUESTIONS if args.mode=='text' else DESKTOP)):
                token=CancellationToken(diagnostic=True);start=time.perf_counter();chunks=[]
                try:
                    answer=runtime.agent.ask(question,on_token=chunks.append,cancellation=token)
                    row=row_for(question,token,start);row['callback']=''.join(chunks)
                    row['desktop_context']=vars(runtime.tools.desktop_context).copy()
                    if args.mode=='desktop':
                        row['foreground']=runtime.tools.execute('get_foreground_window').as_dict()
                        if 'музык' in question.casefold():row['media']=runtime.tools.execute('get_media_sessions').as_dict()
                except Exception:row=dict(input=question,error=traceback.format_exc(),trace=token.trace)
                rows.append(row);save();print(json.dumps({k:v for k,v in row.items() if k in {'input','answer','status','seconds','error'}},ensure_ascii=False),flush=True)
            return
        from kk.voice.factory import build_voice_services
        from kk.voice.stt_faster_whisper import Transcription
        voice=build_voice_services(config,runtime);voice.loop.diagnostic=True
        if args.mode in {'duplex','replay','replay_interrupt'}:
            if args.mode.startswith('replay'):
                from scripts.repair_replay import replay_audio_input
                replay_context=replay_audio_input(source='controlled_sapi_irina_replay')
                replay_source=replay_context.__enter__()
            from kk.ui.overlay import Overlay
            overlay=Overlay(runtime.state,config,voice.loop);overlay.start()
            voice.loop.wakeword=True
            voice_thread=threading.Thread(target=voice.loop.run,name='AcceptanceVoiceRuntime',daemon=True)
            voice_thread.start()
            wait_for(lambda: voice.loop.capture and voice.loop.capture.ready.is_set(),90)
            report['ready_seconds']=time.monotonic()-resource_rows[0]['at']
            report['source']='text dispatch with live Focusrite, normal AEC/interruption and Orb'
            if args.mode=='duplex':
                voice.loop.wake_trigger()
                wait_for(lambda:voice.loop.conversation.active,3)
        else:
            voice.tts.prewarm()
        if args.mode=='fixed':
            report['source']='isolated fixed text -> real SpeechQueue/Silero/PortAudio'
            token=CancellationToken(diagnostic=True)
            speech=voice.loop.speech;speech.begin(token)
            from kk.voice.speech_queue import SentenceBuffer
            buf=SentenceBuffer(lambda text:speech.submit(text,token))
            for word in FIXED.split(' '):buf.feed(word+' ')
            buf.flush();speech.finish(token);speech.wait(token)
            rows.append(dict(input=FIXED,trace=token.trace,spoken_text=speech.spoken_text,first_audio_seconds=speech.first_audio_seconds))
        elif args.mode=='interrupt':
            report['source']='controlled accepted transcript -> BargeInController -> real PortAudio stop (not STT latency)'
            from kk.voice.streaming import BargeInController
            token=CancellationToken(diagnostic=True);speech=voice.loop.speech;speech.begin(token)
            speech.submit(FIXED,token);speech.finish(token);wait_for(lambda:speech.playing.is_set())
            controller=BargeInController(lambda:speech.cancel(reason='confirmed_barge_in',source='acceptance_confirmed_transcript'))
            controller.speech_started(token);time.sleep(.2)
            candidate_kept_playing=speech.playing.is_set() and not token.event.is_set()
            start=time.perf_counter();controller.confirm('стоп',accepted=True,final=True)
            wait_for(lambda:not speech.playing.is_set(),3)
            stop_seconds=time.perf_counter()-start;time.sleep(.5)
            rows.append(dict(candidate_kept_playing=candidate_kept_playing,stop_seconds=stop_seconds,trace=token.trace,stale_audio=speech.playing.is_set()))
        elif args.mode=='replay_interrupt':
            report['source']='controlled SAPI WAV interruption through production VAD/STT/barge-in/dispatch'
            report['input_audio']=replay_source.records
            replay_source.feed_wav(folder/'replay_0.wav',label='initial wake question')
            wait_for(lambda:voice.loop.generation is not None and voice.loop.speech.playing.is_set(),45)
            old=voice.loop.generation
            question='Сколько будет два плюс два?'
            start=time.perf_counter()
            record=replay_source.feed_wav(folder/'replay_5.wav',label='new question while speaking')
            replay_source.wait_empty(30)
            wait_for(lambda:voice.loop.generation is not old,45)
            token=voice.loop.generation
            wait_for(lambda:token.completed.is_set(),60)
            rows.append(row_for(question,token,start))
            new_start=next(e['monotonic'] for e in token.trace if e['event']=='turn_start')
            time.sleep(1)
            report['interruption']=dict(old_generation_cancelled=old.event.is_set(),
                old_generation_completed=old.completed.is_set(),
                old_speech_cancelled=old.speech_token.event.is_set(),
                old_speech_cancel_reason=old.speech_token.cancel_reason,
                old_trace=old.trace,old_speech_trace=old.speech_token.trace,
                stale_audio_after_cancel=any(e['event']=='audio_device_start' and e['monotonic']>next((c['monotonic'] for c in old.speech_token.trace if c['event']=='cancelled'),float('inf')) for e in old.speech_token.trace),
                stale_audio_after_new_turn=any(e['event']=='audio_device_start' and e['monotonic']>new_start for e in old.speech_token.trace),
                input_to_cancel_seconds=next((e['monotonic'] for e in old.speech_token.trace if e['event']=='cancelled'),0)-(record['first_read_at'] or 0))
        elif args.mode=='replay':
            report['source']='controlled SAPI Irina WAV -> paced PCM AudioInput -> production VAD/STT/AEC/wake/handler/TTS'
            report['input_audio']=replay_source.records
            replay_texts=['Оникс, из чего делают чай?', 'А чем зелёный чай отличается от чёрного?',
                          'Открой Firefox', 'Разверни его', 'Почему небо голубое?']
            for index,question in enumerate(replay_texts):
                old=voice.loop.generation;previous_transcript=voice.loop.last_transcription;start=time.perf_counter()
                record=replay_source.feed_wav(folder/f'replay_{index}.wav',label=question)
                replay_source.wait_empty(30)
                try:
                    wait_for(lambda:(voice.loop.generation is not None and voice.loop.generation is not old) or
                        (voice.loop.last_transcription is not previous_transcript and voice.loop.last_transcription is not None
                         and not voice.loop.last_transcription.accepted),20)
                    result=voice.loop.last_transcription
                    if voice.loop.generation is old and result and not result.accepted:
                        from dataclasses import asdict
                        report.setdefault('clarifications',[]).append(dict(input=question,transcription=asdict(result)))
                        if index!=2 or 'low_confidence' not in result.rejection_reason:
                            raise RuntimeError('Replay transcript needs unscripted clarification: '+result.rejection_reason)
                        # The test actor explicitly confirms the intended Firefox command;
                        # production confidence and clarification checks remain untouched.
                        time.sleep(.2);wait_for(lambda:voice.loop.speech.done.is_set(),15)
                        report['clarifications'][-1]['assistant']=voice.loop.speech.spoken_text
                        report['clarifications'][-1]['reply']='Правильно.'
                        replay_source.feed_wav(folder/'replay_6.wav',label='explicit confirmation of Firefox')
                        replay_source.wait_empty(10)
                        wait_for(lambda:voice.loop.generation is not old,15)
                    token=voice.loop.generation
                    wait_for(lambda:any(e['event']=='voice_answer_end' for e in token.trace),180)
                    row=row_for(question,token,start)
                    from dataclasses import asdict
                    row['transcription']=asdict(voice.loop.last_transcription) if voice.loop.last_transcription else None
                    row['pcm_source']=record
                    row['conversation_active']=voice.loop.conversation.active
                    row['audio_end_to_first_callback']=next((e.get('at',e['monotonic']) for e in token.speech_token.trace if e['event']=='audio_first_callback'),0)-(record.get('last_read_perf_counter') or 0)
                except Exception:
                    from dataclasses import asdict
                    row=dict(input=question,error=traceback.format_exc(),pcm_source=record,
                             transcript=getattr(voice.loop.last_transcription,'text',None),
                             transcription=asdict(voice.loop.last_transcription) if voice.loop.last_transcription else None)
                rows.append(row);save()
                print(json.dumps({k:v for k,v in row.items() if k in {'input','answer','status','seconds','error','transcript'}},ensure_ascii=False),flush=True)
                time.sleep(1)

        else:
            for question in (args.utterance or [QUESTIONS[0],QUESTIONS[2],QUESTIONS[4]]):
                evidence=Transcription(question,'ru',1,0,0,committed=True,trace_id=uuid.uuid4().hex)
                start=time.perf_counter();voice.loop.dispatch(question,evidence);token=voice.loop.generation
                wait_for(lambda:any(e['event']=='voice_answer_end' for e in token.trace),180)
                row=row_for(question,token,start);rows.append(row);save()
                print(json.dumps({k:v for k,v in row.items() if k in {'input','answer','status','seconds','first_audio_seconds','cancel_reason'}},ensure_ascii=False),flush=True)
        save()
    except Exception:
        report['fatal']=traceback.format_exc();save();raise
    finally:
        if voice:
            report['components']=dict(brain=runtime.model,stt=voice.stt.model_name,stt_device=getattr(getattr(voice.stt._model,'model',None),'device',None),stt_compute_type=voice.stt.compute_type,tts_voice=voice.tts.actual_voice,tts_rate=voice.tts.sample_rate,tts_device='cpu',aec=voice.loop.echo is not None)
            report['shutdown_phase']='voice_stop';save()
            voice.loop.stop()
            report['shutdown_phase']='speech_close';save()
            voice.loop.speech.close()
            report['shutdown_phase']='voice_join';save()
            if voice_thread:voice_thread.join(6)
            if voice.loop.audio_focus:voice.loop.audio_focus.close()
        if replay_context:replay_context.__exit__(None,None,None)
        report['shutdown_phase']='overlay';save()
        if overlay:
            overlay.stop()
            if overlay.thread:overlay.thread.join(4)
        report['shutdown_phase']='context';save()
        runtime.context.stop();runtime.provider.resource_manager.idle_stop.set()
        sampling_stop.set();sampler.join(3)
        runtime.memory.close();instance.close()
        report['shutdown_phase']='complete'
        report['validation']=validate_report(report);save()
        print(json.dumps({'validation':report['validation']},ensure_ascii=False),flush=True)
        if not report['validation']['passed']:raise SystemExit(2)

if __name__=='__main__':main()
