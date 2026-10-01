"""Guided physical-microphone acceptance using the production loop and Orb."""
from dataclasses import asdict
from threading import Thread
from time import monotonic, sleep
import json

CASES = [
    ('wake_pause', 'Скажи: «Кей Кей… открой… PowerShell». После «открой» помолчи 4 секунды.'),
    ('long_thought', 'Включи mic tap. «Открой PowerShell… потом браузер… хотя сначала браузер, потом PowerShell». Пока НЕ нажимай commit.'),
    ('correction', 'Включи mic tap. «Громкость 100… нет, 50 процентов». Затем commit.'),
    ('ds', 'Скажи: «Кей Кей, DS».'),
    ('tg', 'Скажи: «Кей Кей, TG».'),
    ('gpt', 'Скажи: «Кей Кей, открой браузер и GPT».'),
    ('batch', 'Включи mic tap. «Открой PowerShell на втором, браузер на первом и Discord на втором». Затем commit.'),
    ('mic_tap', 'Включи mic tap. Продиктуй длинный список задач с несколькими паузами. Пока НЕ нажимай commit.'),
    ('mic_hold', 'Зажми маленькую кнопку mic, дождись индикатора записи, скажи команду и отпусти.'),
    ('cancel', 'Включи mic, произнеси команду, нажми Esc. Ничего не должно выполниться; музыка должна восстановиться.'),
    ('drag', 'Перетащи Orb за центр; mic не должен включиться. Начни жест на mic и смести мышь: команда не должна выполниться.'),
    ('followup', 'Скажи: «Кей Кей, DS», затем «Перенеси его на второй», затем «Теперь TG», затем «А GPT в браузере».'),
    ('barge_in', 'Попроси KK дать длинный ответ. Во время TTS начни новую реплику с паузой: «открой… PowerShell».'),
    ('hearthstone', 'Скажи: «Кей Кей, хочу поиграть в Hearthstone». Проверь реальное окно игры. Если игры/launcher нет — skip.'),
]


def run(config, runtime, voice):
    from kk.ui.overlay import Overlay
    from kk.core.state import AgentState
    from kk.permissions.policy import PermissionLevel
    print('LIVE VOICE UX: физический микрофон, реальные действия. WAV не используется.')
    print('Orb: mic tap = запись/commit; hold = запись до отпускания; Esc/правый клик = отмена.')
    print('Отчёт сохраняет только принятые final transcripts, результаты задач и ваши оценки.')
    loop = voice.loop
    loop.wakeword = True
    actions, finals = [], []
    execute, dispatch = runtime.tools.execute, loop.dispatch
    def traced_execute(name, arguments=None):
        stamp = monotonic()
        result = execute(name, arguments)
        if runtime.tools.permission_for(name) and runtime.tools.permission_for(name) >= PermissionLevel.ACTION:
            actions.append(dict(time=stamp, tool=name, ok=result.ok))
        return result
    def traced_dispatch(text, evidence=None):
        if evidence and evidence.committed and evidence.accepted:
            finals.append(dict(time=monotonic(), text=text, pass_name=evidence.chosen_stt_pass,
                               passes=evidence.passes, decode_seconds=evidence.latency_seconds))
        return dispatch(text, evidence)
    runtime.tools.execute, loop.dispatch = traced_execute, traced_dispatch
    thread = Thread(target=loop.run, name='KKLiveAcceptance', daemon=True)
    overlay = Overlay(runtime.state, config, microphone=loop)
    rows=[]
    path=config.root/'data/voice_ux/acceptance.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        thread.start(); overlay.start()
        deadline=monotonic()+120
        while not loop.capture and thread.is_alive() and monotonic()<deadline: sleep(.1)
        if not loop.capture: raise RuntimeError('Voice input did not become ready')
        for name, instruction in CASES:
            loop.mic_cancel()
            input('\nEnter — начать тест '+name+' (или Ctrl+C — завершить): ')
            started=monotonic()
            print(instruction, flush=True)
            before_commit=None
            if name in {'long_thought','mic_tap'}:
                input('После диктовки, ДО commit, нажми Enter здесь: ')
                before_commit=dict(recording=loop.manual_recording,
                    no_actions=not any(a['time']>=started for a in actions),
                    no_dispatch=not any(f['time']>=started for f in finals))
                print('До commit:',before_commit,'Теперь нажми mic для commit.')
            judgement=input('После завершения: pass / fail / skip, затем Enter: ').strip().casefold()
            note=input('Комментарий (можно пустой): ')
            selected_actions=[a for a in actions if a['time']>=started]
            selected_finals=[f for f in finals if f['time']>=started]
            result = 'pass' if judgement=='pass' else 'skip' if judgement=='skip' else 'fail'
            if before_commit and not all(before_commit.values()): result='fail'
            if name=='cancel' and (selected_actions or selected_finals): result='fail'
            if name not in {'cancel','drag'} and result=='pass' and not selected_finals: result='fail'
            if name=='hearthstone' and result=='pass':
                task=runtime.agent.orchestrator.last_task
                if not task or task.status.value!='COMPLETED' or not any(a['tool']=='launch_game' and a['ok'] for a in selected_actions): result='fail'
            batch=getattr(runtime.agent.orchestrator.last_task,'batch',None)
            rows.append(dict(case=name,result=result,note=note,before_commit=before_commit,
                actions=selected_actions,finals=selected_finals,batch=asdict(batch) if batch and name=='batch' else None))
            path.write_text(json.dumps(dict(source='physical_microphone + production VoiceLoop/Orb; human observed',cases=rows),ensure_ascii=False,indent=2),encoding='utf-8')
        print('Отчёт:',path)
        return 0 if all(r['result']=='pass' for r in rows) else 2
    finally:
        loop.stop(); overlay.stop(); thread.join(5)
        runtime.tools.execute, loop.dispatch = execute, dispatch
