"""Real local inference on Onyx intents. This is decision accuracy, NOT live UI success."""
from pathlib import Path
import argparse
import json
import logging
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kk.config import Config
from kk.runtime import build_runtime
from kk.core.orchestrator import SYSTEM_PROMPT
from kk.providers.resources import ModelResourceManager

CASES = [
    ('telegram', 'Открой Telegram.', {'open_application'}),
    ('browser', 'Открой Firefox и YouTube.', {'open_application','open_url'}),
    ('monitor', 'Перенеси браузер на второй экран.', {'move_window_handle_to_monitor','move_window_to_monitor'}),
    ('screen', 'Что сейчас находится на первом экране?', {'see_monitor','get_desktop_context','list_windows','get_monitors'}),
    ('literal', 'Открой PowerShell и напиши ifconfig.', {'open_application','text_entry'}),
    ('execute', 'Открой PowerShell и выполни ipconfig.', {'open_application','text_entry'}),
    ('music', 'Включи музыку.', {'play_music','get_media_sessions','get_current_media','resume_media'}),
    ('yandex', 'Открой Яндекс Музыку и включи любой трек.', {'open_application','play_music','get_media_sessions'}),
    ('latest', 'Открой последний добавленный трек в Яндекс Музыке.', {'play_latest_added_yandex','open_application','get_ui_tree','resolve_application'}),
    ('chat', 'Открой первый чат Telegram.', {'open_first_telegram_chat','open_application'}),
    ('record', 'Начни записывать голосовое в Telegram.', {'start_voice_recording','get_ui_tree'}),
    ('conversation', 'Расскажи мне про ChatGPT.', set()),
    ('error', 'Посмотри на экран и скажи, почему здесь ошибка.', {'observe_context','see_screen','get_ui_tree'}),
    ('video', 'Открой Firefox, найди YouTube и включи любое видео.', {'open_application','open_url','browser_control'}),
    ('codex', 'Открой Codex и перейди к последнему чату.', {'open_application','get_ui_tree'}),
    ('pronoun', 'Разверни его.', {'maximize_window','get_window_state'}),
    ('mixed', 'Открой VS Code и run tests в C:\\ai\\_agent.', {'open_application','run_terminal','list_directory','read_file'}),
]


def score(key, response, expected, allowed):
    calls = response.tool_calls
    if not expected:
        return not calls and len(response.content.split()) >= 35 and response.content.count('.') >= 2
    if not calls or '?' in response.content or any(c.name not in allowed for c in calls):
        return False
    if not any(c.name in expected for c in calls): return False
    for call in calls:
        args = call.arguments
        if not isinstance(args, dict): return False
        if key == 'literal' and call.name in {'type_text','text_entry'}:
            if args.get('text') != 'ifconfig' or args.get('submit'): return False
        if key == 'monitor' and 'move_window' in call.name:
            if args.get('monitor_index') != 2: return False
        if key == 'codex' and call.name == 'run_codex_task': return False
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', default=['qwen2.5:14b','gpt-oss:20b','qwen3:14b'])
    args=parser.parse_args()
    runtime=build_runtime(Config.load(ROOT/'config.toml'), logging.getLogger('benchmark'))
    provider=runtime.provider
    provider.context_window=8192
    provider.timeout=120
    resources=provider.resource_manager
    output=ROOT/'data/onyx_master_benchmark.json'
    report={'basis':'real Ollama inference; first decision + argument quality; no UI actions executed', 'models':{}}
    if output.exists(): report=json.loads(output.read_text(encoding='utf-8'))
    try:
        for model in args.models:
            installed={m.get('name') for m in provider.list_models()}
            if model not in installed:
                report['models'][model]={'available':False,'reason':'not installed / pull failed'}
                output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                continue
            rows=[]
            resources.ensure_unloaded(model)
            for key, request, expected in CASES:
                schemas=runtime.tools.planning_schemas(request) if expected else None
                messages=[{'role':'system','content':SYSTEM_PROMPT + '\nOperational context: current app Firefox, hwnd=42, monitor=1. Preferred browser Firefox; Telegram AyuGram. User explicitly asked for these actions. First step only; do not pretend actions already completed.'},
                          {'role':'user','content':request}]
                started=perf_counter()
                resources.last_health={}
                try:
                    response,timing=provider.chat_stream(model,messages,schemas,temperature=0)
                    row={'case':key,'request':request,'passed':score(key,response,expected,set(runtime.tools.names())),
                         'answer':response.content,'calls':[dict(name=c.name,arguments=c.arguments) for c in response.tool_calls],
                         **timing,'resources':resources.snapshot(),'health':resources.last_health,'schema_count':len(schemas or [])}
                except Exception as exc:
                    row={'case':key,'passed':False,'error':str(exc),'error_type':type(exc).__name__,
                         'health':resources.last_health,'total_seconds':perf_counter()-started}
                rows.append(row)
                report['models'][model]={'available':True,'cases':rows,'passed':sum(r['passed'] for r in rows),
                    'total':len(rows),'median_seconds':statistics.median(r['total_seconds'] for r in rows)}
                output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(model,key,row['passed'],round(row['total_seconds'],2),flush=True)
                from kk.providers.resources import ResourcePressure
                if model in resources.unsuitable or ('error' in row and 'ResourcePressure' in row.get('error_type','')):
                    report['models'][model]['interactive_eligible']=False
                    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    break
            resources.ensure_unloaded(model)
    finally:
        resources.idle_stop.set()
        for row in resources.snapshot()['models']:
            resources.ensure_unloaded(row.get('name') or row.get('model'))
        runtime.memory.close()


if __name__=='__main__': main()
