"""Opt-in live acceptance through Onyx. Records evidence, not just tool return codes.

Never sends messages. Uses fixture files below data/master_live; text/terminal
tests act on the real desktop. Results remain local and are excluded from Git.
"""
from pathlib import Path
import argparse
import json
import logging
import sys
from time import perf_counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kk.config import Config
from kk.runtime import build_runtime
from kk.core.task import TaskStatus
from kk.tools.windows.native import foreground_window, window_metadata


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--groups',nargs='+',choices=['read','desktop','terminal','browser','media','telegram','files','conversation'],default=['read'])
    args=parser.parse_args()
    runtime=build_runtime(Config.load(ROOT/'config.toml'),logging.getLogger('master-live'))
    brain=runtime.agent.orchestrator
    brain.max_tool_rounds=24; brain.timeout_seconds=180
    output=ROOT/'data/onyx_master_live.json'
    report=json.loads(output.read_text(encoding='utf-8')) if output.exists() and output.stat().st_size else []
    def turn(name, request, verify):
        started=perf_counter()
        row=dict(case=name,request=request)
        try:
            row['answer']=runtime.agent.ask(request)
            task=brain.last_task
            evidence=verify(task)
            row.update(passed=task.status==TaskStatus.COMPLETED and evidence.get('verified') is True,
                       status=task.status.value,evidence=evidence,
                       actions=task.actions,metrics=task.metrics)
        except Exception as exc:
            row.update(passed=False,error=str(exc),actions=brain.last_task.actions if brain.last_task else [])
        row['seconds']=perf_counter()-started
        report.append(row)
        temporary=output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report,ensure_ascii=True,indent=2,default=str),encoding='utf-8')
        temporary.replace(output)
        print(name,row['passed'],round(row['seconds'],2),row.get('error',''),flush=True)
        return row
    def last_data(task, tool):
        values=[a['result'].get('data') for a in task.actions if a['tool']==tool and a.get('result',{}).get('ok')]
        return values[-1] if values else {}
    try:
        if 'read' in args.groups:
            for index,word in [(1,'первом'),(2,'втором')]:
                def monitor_check(task,index=index):
                    rows=runtime.tools.execute('list_windows').data
                    matching=[w for w in rows if w.get('monitor_index')==index]
                    return dict(verified=bool(matching) and f'{index}' in (task.result or ''),monitor=index,windows=len(matching))
                turn('screen_'+str(index),f'Что на {word} экране?',monitor_check)
            turn('git','Покажи git status в C:\\ai\\_agent',lambda task:dict(
                verified=bool(last_data(task,'git_status') or last_data(task,'run_terminal')),evidence='actual Git process output'))
        if 'desktop' in args.groups:
            def app_check(task):
                data=last_data(task,'open_application')
                window=window_metadata(data['hwnd']) if data.get('hwnd') else {}
                return dict(verified=bool(window) and foreground_window().get('hwnd')==window.get('hwnd'),window=window)
            turn('tg','Оникс, открой TG.',app_check)
            turn('maximize','Разверни его.',lambda task: runtime.tools.execute('get_window_state').data | {
                'verified':runtime.tools.execute('get_window_state').data.get('maximized') is True})
            turn('firefox','Открой Firefox.',app_check)
            turn('move','Перенеси браузер на второй экран.',lambda task: dict(
                verified=runtime.tools.execute('get_window_state').data.get('monitor_index')==2,
                state=runtime.tools.execute('get_window_state').data))
        if 'terminal' in args.groups or 'desktop' in args.groups:
            typed=turn('literal','Открой PowerShell и напиши ifconfig',lambda task: last_data(task,'text_entry'))
            # Clear only the fixture input before the execution test, preserving the terminal window.
            if typed.get('passed') and foreground_window().get('hwnd')==typed.get('evidence',{}).get('hwnd'):
                runtime.tools.execute('press_hotkey',{'keys':['ctrl','u']})
            turn('execute','Открой PowerShell и выполни ipconfig',lambda task:last_data(task,'text_entry'))
        if 'browser' in args.groups:
            def video_check(task):
                result=runtime.tools.browser.control('verify_playback')
                opened=last_data(task,'open_application')
                if opened.get('hwnd') and result.get('hwnd')!=opened['hwnd']:
                    result.update(verified=False,reason='Playback belongs to another HWND')
                return result
            turn('video','Открой Firefox, открой YouTube и включи любое видео.',video_check)
            turn('gpt','Открой браузер и GPT.',lambda task:last_data(task,'open_url'))
        if 'media' in args.groups:
            def playing(task):
                rows=runtime.tools.execute('get_media_sessions').data
                matches=[s for s in rows if ('yandex' in str(s).casefold() or 'яндекс' in str(s).casefold()) and
                         (s.get('playing') or (s.get('peak',0)>.001 and not s.get('muted')))]
                return dict(verified=bool(matches),media=matches)
            turn('music','Включи музыку.',lambda task:dict(verified=any(s.get('playing') or
                (s.get('peak',0)>.001 and not s.get('muted')) for s in runtime.tools.execute('get_media_sessions').data)))
            turn('yandex','Открой Яндекс Музыку и включи любой трек.',playing)
            turn('latest','Включи последний добавленный трек',lambda task:last_data(task,'play_latest_added_yandex'))
        if 'telegram' in args.groups:
            turn('first_chat','Открой первый чат Telegram.',lambda task:last_data(task,'open_first_telegram_chat'))
            try:
                turn('record','Начни записывать голосовое',lambda task:last_data(task,'start_voice_recording'))
            finally:
                if runtime.tools.telegram.recording_hwnd:
                    turn('cancel_record','Отмени голосовое',lambda task:last_data(task,'finish_voice_recording'))
        if 'files' in args.groups:
            from uuid import uuid4
            folder=ROOT/'data/master_live'/uuid4().hex; folder.mkdir(parents=True)
            source=folder/'fixture.txt'; source.write_text('Onyx fixture',encoding='utf-8')
            destination=folder/'moved.txt'
            turn('find_file',f'Найди fixture.txt в {folder}',lambda task:dict(verified=source.is_file() and
                (any(f.get('path')==str(source) for f in last_data(task,'search_files').get('files',[])) or
                 str(source) in last_data(task,'run_terminal').get('stdout',''))))
            turn('move_file',f'Перенеси файл {source} в {destination}',lambda task:dict(
                verified=destination.is_file() and not source.exists() and destination.read_text(encoding='utf-8')=='Onyx fixture'))
        if 'conversation' in args.groups:
            turn('chatgpt_answer','Расскажи мне про ChatGPT.',lambda task:dict(
                verified=len((task.result or '').split())>=35 and (task.result or '').count('.')>=2,
                words=len((task.result or '').split()),sentences=(task.result or '').count('.')))
    finally:
        runtime.memory.close()
        manager=getattr(runtime.provider,'resource_manager',None)
        if manager:
            manager.idle_stop.set()
            for m in manager.snapshot()['models']:
                manager.ensure_unloaded(m.get('name') or m.get('model'))


if __name__=='__main__': main()
