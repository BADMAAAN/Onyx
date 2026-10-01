"""Bounded serial acceptance; production model/handlers, explicitly labelled spies.

Run with normal Onyx stopped. Private diagnostic output is never committed.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse
import json
import logging
import threading
import time
from copy import deepcopy
from unittest.mock import patch
from hashlib import sha256

from kk.config import Config
from kk.core.instance import SingleInstance
from kk.core.task import CancellationToken
from kk.runtime import build_runtime
from kk.voice.factory import build_voice_services
from scripts.replay_real_speech import wait_for
from scripts.replay_diagnostics import ReplayProbe
from scripts.repair_replay import replay_audio_input

# Expectations are defined before any model call. Names are never STT prompts.
TEXT_CASES=[
 ('RU','Из чего делают чай?',[]),
 ('EN','What is tea made from?',[]),
 ('RU','Объясни разницу между RAM и VRAM.',[]),
 ('EN','Can you explain what a Python function does?',[]),
 ('RU_NEG','Не закрывай VS Code',[]),
 ('EN_NEG',"Don't close VS Code",[]),
 ('RU_NEG','Не закрывай VS Code, открой Блокнот',['open_application']),
 ('EN_NEG',"Don't close Firefox; open Notepad instead",['open_application']),
 ('RU_NEG','Открой Discord... нет, не открывай',[]),
 ('RU_CORRECTION','Открой Firefox... нет, лучше Obsidian',['open_application']),
 ('RU_POLITE','Не мог бы ты открыть Блокнот?',['open_application']),
 ('MIXED','Open Notepad and type: сегодня я изучаю Python',['open_application','text_entry']),
 ('MIXED','Открой Obsidian and type: meeting at five',['open_obsidian_draft']),
 ('LITERAL','Напиши в Блокноте: не закрывай VS Code',['open_application','text_entry']),
 ('YOUTUBE_ACTION','Открой YouTube',['open_url']),
 ('YOUTUBE_TALK','Расскажи про YouTube',[]),
 ('YOUTUBE_LITERAL','Напиши YouTube',['text_entry']),
 ('YOUTUBE_REPEAT','Повтори слово YouTube',[]),
]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['text','delivery','replay'])
    p.add_argument('--label',required=True)
    p.add_argument('--live-calculator',action='store_true',help='One safe owned Calculator launch via replay; all other actions stay spies')
    p.add_argument('--case',help='Replay case id from the previously saved manifest')
    p.add_argument('--live-folder',action='store_true',help='Open the isolated acceptance folder only')
    args=p.parse_args()
    if not args.label.replace('_','').isalnum(): p.error('alphanumeric label required')
    folder=ROOT/'data/voice_final'; folder.mkdir(exist_ok=True)
    output=folder/(args.mode+'_'+args.label+'.json')
    if output.exists(): p.error('preserve previous evidence; use a new label')
    report=dict(mode=args.mode,rows=[],expectations=TEXT_CASES,human_speech='HUMAN_UNVERIFIED')
    def compact(value):
        if isinstance(value,dict): return {k:compact(v) for k,v in value.items() if k not in {'messages','context'}}
        if isinstance(value,list): return [compact(v) for v in value]
        return value
    def save(): output.write_text(json.dumps(compact(report),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    save()
    instance=SingleInstance(ROOT/'data/kk.lock')
    runtime=voice=thread=replay=None
    owned_windows=[]
    logger=logging.getLogger('voice-final'); logger.addHandler(logging.NullHandler())
    try:
        config=Config.load(ROOT/'config.toml')
        runtime=build_runtime(config,logger)
        runtime.policy.clear_stop()
        # All side effects use executor fixtures. No browser/desktop/library writes.
        calls=[]
        from kk.tools.windows.windows import list_visible_windows
        from kk.tools.windows.app_resolver import canonical
        initial_calculators=[w for w in list_visible_windows() if 'calculator' in str(w.get('process','')).casefold()]
        initial_handles={w['hwnd'] for w in list_visible_windows()}
        active_case=None
        from kk.permissions.policy import PermissionLevel
        for name,tool in runtime.tools._tools.items():
            if tool.permission >= PermissionLevel.ACTION:
                def spy(_name=name,_handler=tool.handler,**kw):
                    calls.append(dict(tool=_name,arguments=kw))
                    if (args.live_folder and active_case=='owned_folder' and _name=='open_desktop_target'
                            and kw.get('type')=='folder' and kw.get('name','').casefold()=='голосовая проверка'
                            and kw.get('location')=='downloads'):
                        result=_handler(**kw)
                        report['live_folder']=result
                        if result.get('verified') and result.get('hwnd') not in initial_handles:
                            from kk.tools.windows.native import window_metadata
                            window=window_metadata(result['hwnd'])
                            owned_windows.append(dict(hwnd=window['hwnd'],process_id=window['process_id']))
                        return result
                    if (args.live_calculator and active_case=='ru_open' and _name=='open_application'
                            and canonical(kw.get('name','')) in {'calculator','calc','калькулятор'}):
                        if initial_calculators:
                            raise RuntimeError('Existing Calculator belongs to user; live fixture skipped')
                        result=_handler(**kw)
                        if result.get('verified') and result.get('hwnd'):
                            owned_windows.append(dict(hwnd=result['hwnd'],process_id=result.get('process_id')))
                        report['live_calculator']=result
                        return result
                    return dict(verified=True,hwnd=12345,process_id=12345,monitor_index=1,
                        name=kw.get('name') or kw.get('query'),text=kw.get('text'),source='executor_spy',
                        evidence='fixture window identity and visible state verified')
                tool.handler=spy
        # Fixed observation makes direct/model/production comparisons repeatable.
        runtime.tools._tools['observe_context'].handler=lambda: {'source':'fixture','windows':[], 'monitors':[{'index':1},{'index':2}]}
        if args.mode=='text':
            chat=runtime.provider.chat_stream
            captured=[]
            def capture(*a,**kw):
                if not captured: captured.append((deepcopy(a),{k:deepcopy(v) for k,v in kw.items() if k not in {'cancellation','on_token'}}))
                return chat(*a,**kw)
            runtime.provider.chat_stream=capture
            with patch('kk.tools.windows.native.foreground_window',return_value={'process':'fixture.exe'}):
                for index,(language,text,expected) in enumerate(TEXT_CASES):
                    calls.clear(); token=CancellationToken(diagnostic=True); chunks=[]
                    start=time.perf_counter()
                    answer=runtime.agent.ask(text,on_token=chunks.append,cancellation=token)
                    task=runtime.agent.orchestrator.last_task
                    report['rows'].append(dict(language=language,input=text,expected_tools=expected,
                        actual_tools=[c['tool'] for c in calls],calls=list(calls),answer=answer,
                        callback_complete=''.join(chunks)==answer,seconds=time.perf_counter()-start,
                        status=task.status.value,trace=token.trace,metrics=task.metrics))
                    save(); print(json.dumps({k:report['rows'][-1][k] for k in ('language','input','actual_tools','answer','seconds')},ensure_ascii=False),flush=True)
                    if index==0 and captured:
                        a,kw=captured[0]; start=time.perf_counter()
                        direct,direct_timings=chat(*a,**kw)
                        report['direct_same_contract']=dict(content=direct.content,seconds=time.perf_counter()-start,
                            request_sha256=sha256(json.dumps([a,kw],ensure_ascii=False,default=str,sort_keys=True).encode()).hexdigest(),
                            comparison='identical captured model/messages/tools/options/history; no tool execution')
                        save()
        else:
            voice=build_voice_services(config,runtime)
            voice.loop.diagnostic=True
            replay=replay_audio_input(source='controlled_voice_final_synthetic')
            source=replay.__enter__()
            thread=threading.Thread(target=voice.loop.run,daemon=True); thread.start()
            wait_for(lambda:voice.loop.capture and voice.loop.capture.ready.is_set(),90)
            report['tts']=voice.tts.runtime_diagnostics()
            report['stt']=dict(model=voice.stt.model_name,device=voice.stt.device,compute_type=voice.stt.compute_type)
            loaded=voice.stt._model.model
            report['stt'].update(actual_device=loaded.device,actual_compute_type=loaded.compute_type,
                language=voice.stt.language,final_options=voice.stt.final_options,retry_options=voice.stt.retry_options)
            if args.mode=='delivery':
                for text in ('Из чего делают чай?', 'What is tea made from?'):
                    start=time.perf_counter(); token=voice.loop.dispatch_text(text,spoken=True,diagnostic=True)
                    if not token.completed.wait(120): raise TimeoutError('turn completion')
                    report['rows'].append(dict(input=text,seconds=time.perf_counter()-start,**token.result_snapshot))
                    save(); print(json.dumps(dict(input=text,answer=token.answer),ensure_ascii=False),flush=True)
                token=voice.loop.dispatch_text('Explain what a Python function does in four sentences.',spoken=True,diagnostic=True)
                wait_for(lambda:voice.loop.speech.playing.is_set(),90)
                start=time.perf_counter()
                voice.loop.interrupt(reason='explicit_stop',source='acceptance_stop')
                wait_for(lambda:not voice.loop.speech.playing.is_set(),3)
                report['stop']=dict(seconds=time.perf_counter()-start,task_trace=token.trace,speech_trace=token.speech_token.trace)
                token.completed.wait(10); save()
            else:
                manifest=json.loads((folder/'synthetic_manifest.json').read_text(encoding='utf-8'))
                probe=ReplayProbe(voice.loop)
                for row in manifest:
                    if args.case and row['id'] != args.case: continue
                    active_case=row['id']
                    calls.clear(); probe.begin_case(row['id'])
                    voice.loop.conversation.touch()
                    source.feed_wav(folder/row['file'],label='synthetic_'+row['id'])
                    source.wait_empty(25)
                    # Decoder completion precedes consumption of the final event.
                    # Require stability before snapshot/next input, as in the original replay harness.
                    stable_since=None
                    def settled():
                        nonlocal stable_since
                        if probe.settled():
                            if stable_since is None: stable_since=time.monotonic()
                            return time.monotonic()-stable_since >= .75
                        stable_since=None
                        return False
                    wait_for(settled,90)
                    report['rows'].append(dict(fixture=row,**probe.snapshot(),executor_calls=list(calls)))
                    save(); print(row['id']+' settled',flush=True)
                    time.sleep(.3)
    finally:
        if owned_windows:
            import ctypes
            from ctypes import wintypes
            cleanup=[]
            for window in owned_windows:
                process=wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(window['hwnd'],ctypes.byref(process))
                matched=process.value==window['process_id']
                if matched:
                    ctypes.windll.user32.PostMessageW(window['hwnd'],0x0010,0,0)
                    deadline=time.monotonic()+3
                    while ctypes.windll.user32.IsWindow(window['hwnd']) and time.monotonic()<deadline: time.sleep(.05)
                cleanup.append(dict(**window,identity_matched=matched,closed=not bool(ctypes.windll.user32.IsWindow(window['hwnd']))))
            report['owned_fixture_cleanup']=cleanup
        if voice:
            voice.loop.stop(); voice.loop.speech.close()
            if thread: thread.join(8)
            if voice.loop.audio_focus: voice.loop.audio_focus.close()
        if replay: replay.__exit__(None,None,None)
        if runtime:
            runtime.context.stop(); runtime.provider.resource_manager.idle_stop.set(); runtime.memory.close()
        instance.close(); report['shutdown']='complete'; save()


if __name__=='__main__': main()
