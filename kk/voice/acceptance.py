"""Human-operated physical microphone acceptance. Never substitutes a WAV."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from time import perf_counter
from kk.core.task import CancellationToken
from .speech_queue import SentenceBuffer
from .loop import is_stop


COMMANDS = [
    'Кей Кей, привет',
    'Кей Кей, открой блокнот',
    'Напиши в блокноте: Кей Кей работает',
    'Что сейчас открыто?',
    'Что написано в блокноте?',
    'Открой настройки Windows',
    'Найди Bluetooth',
    'Вернись в блокнот',
    'Какая сейчас громкость?',
    'Стоп',
]


def run_acceptance(config, runtime, voice):
    report = config.root/'docs'/'LIVE_ACCEPTANCE.md'
    raw = config.root/'data'/'live_acceptance.json'
    results = []
    def save():
        payload = {'date':datetime.now(timezone.utc).isoformat(), 'source':'physical_microphone',
            'device':config.get('voice.microphone'), 'complete':len(results)==10 and all(r.get('human_pass') for r in results),
            'trials':results}
        raw.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        lines = ['# Live microphone acceptance', '', f"Device: {payload['device']}",
            f"Date: {payload['date']}", f"Complete: {payload['complete']}",
            '', 'Проверка человеком обязательна. WAV и ненулевой сигнал не считаются live acceptance.', '',
            '| Команда | Transcription | STT s | Brain first token s | First tool s | First audio s | Total s | Human pass |',
            '|---|---|---:|---:|---:|---:|---:|---|']
        for r in results:
            lines.append(f"| {r['expected']} | {r.get('transcription',r.get('error','')).replace('|','/')} | {r.get('stt_seconds','—')} | {r.get('brain_first_token_seconds','—')} | {r.get('first_tool_call_seconds','—')} | {r.get('first_audio_seconds','—')} | {r.get('total_seconds','—')} | {r.get('human_pass',False)} |")
        lines += ['', 'Подробные VAD события, intent, tool calls, verification и ответы: `data/live_acceptance.json` (локально, исключён из Git).']
        report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('LIVE ACCEPTANCE — физический Focusrite. Ctrl+C: Emergency Stop. WAV не используется.')
    print('Для акустического теста выключите музыку; приготовьтесь говорить после приглашения.')
    import keyboard
    stop_hook=keyboard.add_hotkey('ctrl+alt+pause',voice.loop.emergency_stop,suppress=False)
    voice.stt._load()
    voice.tts.prewarm()
    try:
        for index, command in enumerate(COMMANDS,1):
            input(f'\n[{index}/10] Нажмите Enter, дождитесь звукового сигнала, затем скажите: {command}\n')
            events = []
            last_meter = [0]
            def event(kind,data):
                if kind != 'level' or data['seconds']-last_meter[0] >= .3:
                    print(f'[MIC {kind}] {data}',flush=True)
                    events.append({'event':kind,**data})
                    last_meter[0] = data.get('seconds',0)
            trial = {'expected':command,'events':events}
            started = perf_counter()
            try:
                heard = voice.stt.listen(on_event=event,calibration_seconds=1.5,
                    ready_beep=True,**voice.loop.record_options)
                capture_stt_seconds = perf_counter()-started
                trial.update(transcription=heard.text,stt_seconds=heard.latency_seconds)
                print(f'[TRANSCRIPTION] {heard.text}\n[STT latency] {heard.latency_seconds:.3f}s',flush=True)
                if input('Распознано именно ваше произнесение? Enter=да, n=пропустить: ').casefold() == 'n':
                    trial['human_pass'] = False
                    results.append(trial)
                    save()
                    continue
                # Processing latency excludes the human confirmation interval.
                processed = perf_counter()
                if is_stop(heard.text):
                    voice.loop.emergency_stop()
                    trial['intent'] = 'emergency_stop'
                    trial['answer'] = 'Остановлено'
                else:
                    token = CancellationToken()
                    voice.loop.speech.begin(token)
                    chunks = SentenceBuffer(lambda c:voice.loop.speech.submit(c,token))
                    answer = runtime.agent.ask(heard.text,on_token=chunks.feed,cancellation=token)
                    chunks.flush()
                    voice.loop.speech.finish(token)
                    voice.loop.speech.wait(token)
                    task = runtime.agent.orchestrator.last_task
                    actions = task.actions
                    trial.update(intent=task.user_request, tool_calls=task.actions,
                        verification=task.observations, answer=answer,
                        selected_tools=[action['tool'] for action in actions],
                        tool_execution=[{'tool':action['tool'],
                            'execution_seconds':action.get('execution_seconds'),
                            'verification_seconds':action.get('verification_seconds'),
                            'ok':action.get('result',{}).get('ok')} for action in actions],
                        brain_route=task.metrics.get('brain_route'),
                        brain_first_token_seconds=task.metrics.get('brain_first_token_seconds'),
                        first_tool_call_seconds=task.metrics.get('first_tool_call_seconds'),
                        task_status=task.status.value,
                        first_audio_seconds=voice.loop.speech.first_audio_seconds)
                trial['processing_seconds'] = perf_counter()-processed
                trial['total_seconds'] = capture_stt_seconds+trial['processing_seconds']
                trial['latency_definition'] = 'capture + STT + processing/playback; human confirmation excluded'
                print(json.dumps({k:v for k,v in trial.items() if k!='events'},ensure_ascii=False,indent=2,default=str))
                trial['human_pass'] = input('Действие, проверка и голос верны? y=да: ').casefold() == 'y'
            except Exception as exc:
                trial['error'] = f'{type(exc).__name__}: {exc}'
                trial['human_pass'] = False
                print(trial['error'])
            results.append(trial)
            save()
    except (KeyboardInterrupt, EOFError):
        runtime.policy.trigger_stop()
        voice.loop.interrupt()
        save()
        return 130
    finally:
        keyboard.remove_hotkey(stop_hook)
        voice.loop.speech.close()
    return 0 if all(r.get('human_pass') for r in results) else 1
