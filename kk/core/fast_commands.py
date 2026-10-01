"""Conservative exact intent routes; every action still goes through the registry."""
from __future__ import annotations

import re
from time import monotonic, perf_counter
from kk.core.task import TaskStatus
from kk.core.state import AgentState


def normalize(text):
    return re.sub(r'^(?:оникс|onyx|кей[\s-]+кей|окей[\s-]+окей|kk)\b[ ,.!?-]*','',text.strip(),flags=re.I).strip(' .!?')


def route_fast(text, task, tools, state, performance=None):
    """Keep the legacy helper internal; the planner consumes a typed outcome."""
    from .route_result import RouteResult
    answer = run_fast(text, task, tools, state, performance)
    for action in reversed(task.actions):
        data = action.get('result',{}).get('data')
        if isinstance(data,dict) and data.get('status') == 'needs_clarification':
            answer, task.status = data['question'], TaskStatus.WAITING
            if hasattr(task,'batch'):
                task.batch.status, task.batch.result = 'waiting', answer
            break
    status = ('not_handled' if answer is None else
              'error' if task.status == TaskStatus.FAILED else 'handled')
    result = RouteResult(status, answer, task.metrics.get('brain_route'))
    task.metrics['fast_route_status'] = status
    task.cancellation_token.mark('fast_route_result', status=status, route=result.route,
        transcript=text, intent=task.metrics.get('desktop_plan'), answer=answer)
    return result


def run_fast(text, task, tools, state, performance=None):
    if task.metrics.get('pending_target_intent'):
        from .target_confirmation import run
        return run(task.metrics['pending_target_intent'], task, tools, state)
    command = normalize(text)
    folded = command.casefold()
    from .memory_routes import route as memory_route
    memory_answer = memory_route(command, task, tools)
    if memory_answer is not None:
        return memory_answer
    if not folded:
        state.set(AgentState.LISTENING, 'Слушаю…')
        return ''
    from .literal_intents import run_literal
    literal = run_literal(text, task, tools)
    if literal is not None: return literal
    if task.metrics.get('context_needs_planner'): return None
    # Negation remains part of the full request sent to Brain; no partial clause
    # may act before the semantic planner has resolved its scope.
    from .command_language import has_unquoted_negation
    if has_unquoted_negation(command):
        scope = task.request_scope
        if (scope.get('allowed') and not scope.get('unresolved') and
                all(s['kind'] == 'open' and s.get('monitor') is None for s in scope['allowed'])):
            from .desktop_commands import run_desktop, DesktopContext
            if not hasattr(tools, 'desktop_context'): tools.desktop_context = DesktopContext()
            return run_desktop(text, task, tools, state, tools.desktop_context, plan=scope['allowed'])
        return None
    from .collection_routes import route_collection
    collection = route_collection(command, task, tools)
    if collection is not None: return collection
    from .context_routes import route
    contextual = route(command, task, tools, state)
    if contextual is not None: return contextual
    if task.metrics.get('context_needs_planner'): return None
    # A correction belongs to the whole turn, never to an early exact route.
    if re.search(r'\b(?:нет|не надо|ничего не|хотя|actually|instead|cancel)\b', folded):
        from .desktop_commands import parse_plan, run_desktop, DesktopContext
        intents = getattr(tools, 'personal_intents', None)
        from .personal_intents import PersonalIntentRegistry
        if not isinstance(intents, PersonalIntentRegistry): intents = None
        if parse_plan(text, getattr(tools, 'desktop_context', None), intents):
            if not hasattr(tools, 'desktop_context'): tools.desktop_context = DesktopContext()
            return run_desktop(text, task, tools, state, tools.desktop_context)
        return None
    if folded in {'привет','здравствуй','здравствуйте'}:
        task.metrics.update(brain_route='deterministic_no_llm',brain_first_token_seconds=None)
        task.cancellation_token.mark('intent_decided',route='deterministic_no_llm')
        return 'Привет. Я слушаю.'
    calls = []
    kind = None
    expected = None
    context = getattr(tools, 'desktop_context', None)
    if folded in {'выключи его','включи его','выключи bluetooth','включи bluetooth','выключи блютуз','включи блютуз'} and (
            'bluetooth' in folded or 'блютуз' in folded or (context and context.recent() and context.last_entity=='bluetooth')):
        kind, calls = 'bluetooth_toggle', [('open_settings',{'page':'bluetooth'}),('set_ui_toggle',{'name':'Bluetooth','enabled':folded.startswith('включи')})]
    elif folded in {'сделай себя невидимой', 'скройся', 'убери себя', 'спрячь кружок', 'убери orb', 'сделай себя невидимым', 'скрой orb', 'скрой шар', 'hide yourself'}:
        kind, calls = 'orb_hidden', [('set_orb_visibility', {'visible': False})]
    elif folded in {'сделай себя видимой', 'покажись', 'верни кружок', 'сделай себя видимым', 'покажи orb', 'покажи шар', 'show yourself'}:
        kind, calls = 'orb_visible', [('set_orb_visibility', {'visible': True})]
    elif folded in {'сделай музыку тише','музыку тише','музыку чуть тише','сделай музыку громче','музыку громче','музыку чуть громче'}:
        kind, calls = 'media_volume', [('adjust_media_volume', {'delta':-10 if 'тише' in folded else 10})]
    elif folded in {'выключи музыку','музыку выключи','включи обратно','следующий трек'}:
        tool={'выключи музыку':'pause_media','музыку выключи':'pause_media','включи обратно':'resume_media','следующий трек':'next_track'}[folded]
        kind, calls = 'media', [(tool,{})]
    elif folded in {'какая у меня громкость','какая сейчас громкость','какая громкость'}:
        kind, calls = 'volume', [('get_volume',{})]
    elif folded in {'что сейчас открыто','какое окно сейчас открыто'}:
        kind, calls = 'foreground', [('get_foreground_window',{})]
    elif folded in {'что сейчас находится на обоих экранах','что находится на обоих экранах','что открыто на обоих экранах'}:
        kind, calls = 'both_monitors', [('get_desktop_context',{})]
    elif folded in {'что я сейчас делал','что я делал'}:
        kind, calls = 'history', [('get_recent_context',{'duration':600})]
    elif re.fullmatch(r'верни меня туда,? где я был до (?:telegram|телеграм|телеграма|ayugram)', folded):
        kind, calls = 'return', [('return_to_previous_window',{'before_app':'AyuGram'})]
    elif folded in {'где telegram','где телеграм','где ayugram','где открыт telegram','где открыт телеграм'}:
        kind, calls = 'locate', [('find_app_in_context',{'query':'AyuGram'})]
    elif folded in {'поставь мониторинг на паузу','приостанови мониторинг'}:
        kind, calls = 'pause', [('pause_ambient_monitoring',{'paused':True})]
    elif folded in {'возобнови мониторинг','включи мониторинг'}:
        kind, calls = 'pause', [('pause_ambient_monitoring',{'paused':False})]
    elif folded in {'открой блокнот','открыть блокнот'}:
        kind, calls = 'notepad', [('open_application',{'name':'notepad'})]
    elif folded in {'открой настройки windows','открой настройки виндовс','открой настройки'}:
        kind, calls = 'settings', [('open_settings',{})]
    elif folded in {'найди bluetooth','открой bluetooth','bluetooth','блютуз'}:
        kind, calls = 'bluetooth', [('open_settings',{'page':'bluetooth'})]
    elif folded in {'открой настройки windows и найди bluetooth','открой настройки windows, найди bluetooth и скажи, включён ли он'}:
        kind, calls = 'bluetooth', [('open_settings',{'page':'bluetooth'})]
    elif folded in {'что написано в текущем окне','что написано в этом окне'}:
        kind,calls = 'read', [('see_screen',{'question':text,'mode':'auto'})]
    elif folded in {'что написано в блокноте','прочитай блокнот'}:
        kind,calls = 'read', [('focus_window',{'title':'Блокнот'}),
                              ('see_screen',{'question':text,'mode':'auto'})]
    elif folded in {'вернись в блокнот','переключись на блокнот'}:
        kind,calls = 'focus_notepad', [('focus_window',{'title':'Блокнот'})]
    else:
        match = re.fullmatch(r'открой блокнот и напиши\s*[: ]\s*["«]?(.*?)["»]?$',command,flags=re.I)
        if match and match[1]:
            expected = match[1]
            kind,calls = 'write', [('open_application',{'name':'notepad'}),('type_text',{'text':expected})]
        else:
            match = re.fullmatch(r'(?:найди блокнот и )?напиши (?:в блокноте|туда)\s*[: ]\s*["«]?(.*?)["»]?$',command,flags=re.I)
            if match and match[1]:
                expected = match[1]
                kind,calls = 'write', [('focus_window',{'title':'Блокнот'}),('type_text',{'text':expected})]
    if kind is None:
        from .desktop_commands import DesktopContext, run_desktop
        if not hasattr(tools, 'desktop_context'):
            tools.desktop_context = DesktopContext()
        return run_desktop(text, task, tools, state, tools.desktop_context)
    start=perf_counter()
    task.metrics['brain_route']='deterministic_no_llm'
    task.metrics['brain_first_token_seconds']=None
    task.cancellation_token.mark('intent_decided',route='deterministic_no_llm')
    observation={}
    result=None
    for name,args in calls:
        task.before_action(name,args,observation)
        state.set(AgentState.ACTING, 'Пишу в Блокноте…' if name=='type_text' else 'Выполняю…')
        if performance and task.current_step==1:
            performance.record('first_tool_call',perf_counter()-start,task_id=task.id,route='deterministic')
        if task.current_step==1:
            task.metrics['first_tool_call_seconds']=perf_counter()-start
        before=perf_counter()
        task.cancellation_token.mark('tool_start',tool=name)
        result=tools.execute(name,args).as_dict()
        task.cancellation_token.mark('tool_end',tool=name,ok=result['ok'])
        task.actions[-1]['execution_seconds']=perf_counter()-before
        task.actions[-1]['result']=result
        if performance:
            performance.record('simple_tool_execution',perf_counter()-before,tool=name)
        task.check()
        if not result['ok']:
            task.status=TaskStatus.FAILED
            return 'Не удалось выполнить: '+result['error']
        if name in {'open_application','open_settings','type_text'}:
            task.cancellation_token.mark('verification_start',tool=name)
            task.status=TaskStatus.VERIFYING
            deadline=monotonic()+5
            verified=False
            while monotonic()<deadline:
                task.check()
                if name=='open_application':
                    # Opening Notepad needs HWND/process/monitor verification,
                    # not a traversal of every edit control and restored tab.
                    metadata=tools.execute('get_foreground_window').as_dict()
                    observation={'ok':metadata['ok'],'error':metadata.get('error'),
                        'data':{'foreground':metadata.get('data') or {},'source':'window_metadata'}}
                else:
                    observation=tools.execute('observe_context').as_dict()
                data=observation.get('data') or {}
                fg=data.get('foreground',{})
                exe=fg.get('executable','').casefold()
                if name=='open_application':
                    verified=exe.endswith('notepad.exe') and bool(fg.get('hwnd') and fg.get('monitor_id'))
                elif name=='open_settings':
                    verified=exe.endswith('systemsettings.exe') or (exe.endswith('applicationframehost.exe') and
                        any(e.get('automation_id','').startswith('SystemSettings_') for e in data.get('ui_elements',[])))
                    if kind=='bluetooth':
                        verified=verified and any('bluetooth' in str(e.get('name','')).casefold() for e in data.get('ui_elements',[]))
                else:
                    verified=exe.endswith('notepad.exe') and any(expected in str(e.get('text') or e.get('value') or '') for e in data.get('ui_elements',[]))
                if verified:
                    break
                task.cancellation_token.event.wait(.15)
            task.actions[-1]['verification_seconds']=max(0,5-(deadline-monotonic()))
            task.observations.append(observation)
            task.actions[-1]['verification']={'verified':verified,'observation':observation}
            task.cancellation_token.mark('verification_end',tool=name,verified=verified)
            if not verified:
                task.status=TaskStatus.FAILED
                return 'Действие вызвано, но результат не подтверждён в интерфейсе. Останавливаюсь.'
    data=result['data']
    if kind in {'settings','bluetooth','bluetooth_toggle'}:
        from .desktop_commands import DesktopContext
        context = getattr(tools,'desktop_context',None) or DesktopContext()
        context.app='Settings'; context.updated=monotonic()
        context.last_entity='bluetooth' if kind.startswith('bluetooth') else 'settings'
        fg=(observation.get('data') or {}).get('foreground',{})
        context.hwnd=fg.get('hwnd',0); context.process_id=fg.get('process_id',0)
        context.last_monitor=fg.get('monitor_index'); context.last_action=kind
        tools.desktop_context=context
    if kind == 'bluetooth_toggle':
        if not data.get('verified'):
            task.status=TaskStatus.FAILED
            return 'Состояние Bluetooth не подтверждено.'
        return 'Bluetooth '+('включён.' if data['enabled'] else 'выключен.')
    if kind == 'media_volume':
        return f"Музыка: {data['percent']} процентов." if data.get('verified') else 'Изменение громкости не подтверждено.'
    if kind == 'media':
        if not data.get('verified'): task.status=TaskStatus.FAILED
        return 'Готово.' if data.get('verified') else 'Команда передана плееру; результат не подтверждён.'
    if kind in {'orb_hidden', 'orb_visible'}:
        return 'Я здесь.' if kind == 'orb_visible' else 'Скрыл шар. Я продолжаю слушать.'
    if kind=='volume':
        return f"Громкость {data['percent']} процентов"+(', звук выключен.' if data['muted'] else '.')
    if kind=='foreground':
        return 'Сейчас открыто: '+(data.get('title') or 'окно без заголовка')+'.'
    if kind=='both_monitors':
        groups = {}
        for windows in data.get('windows_by_monitor', {}).values():
            for window in windows:
                label = window.get('process') or 'приложение'
                if window.get('title'):
                    label += ' — ' + window['title']
                groups.setdefault(window.get('monitor_index'), []).append(label)
        if not groups:
            return 'Видимые окна на мониторах не найдены.'
        return ' '.join(f"Монитор {index}: {', '.join(titles[:5])}." for index,titles in sorted(groups.items(), key=lambda pair: str(pair[0])) if index) or 'Не удалось определить расположение окон.'
    if kind=='history':
        summary = data['summary']
        activities = [a for a in summary['activities'] if a.get('process')]
        current = summary.get('current_activity') or (activities[0] if activities else None)
        previous = summary.get('previous_activity')
        if not current:
            return 'В локальной истории пока недостаточно данных об активности.'
        answer = f"Сейчас активно окно {current.get('process') or 'приложения'} ({current.get('title') or 'без заголовка'})"
        if current.get('monitor_id'):
            answer += f" на {current['monitor_id']}"
        if previous:
            answer += f". Перед этим было активно {previous.get('process') or 'приложение'} ({previous.get('title') or 'окно без заголовка'})"
        return answer+'.'
    if kind=='return':
        window = data['window']
        return f"Вернул тебя в {window.get('process') or 'окно'}: {window.get('title') or 'без заголовка'} на {window.get('monitor_id') or 'текущем мониторе'}."
    if kind=='locate':
        if not data['found']:
            return 'Telegram не найден в текущем контексте.'
        window = data['window']
        return f"Telegram {'открыт' if data['current'] else 'ранее был открыт'} на мониторе {window.get('monitor_index') or window.get('monitor_id') or '?'}: {window.get('title') or 'окно без заголовка'}."
    if kind=='pause':
        return 'Ambient Monitoring приостановлен.' if data['paused'] else 'Ambient Monitoring возобновлён.'
    if kind=='read':
        return data.get('description') or 'Не удалось прочитать содержимое окна.'
    if kind=='bluetooth':
        elements=(observation.get('data') or {}).get('ui_elements',[])
        toggles=[e for e in elements if 'bluetooth' in e.get('name','').casefold() and 'toggle_state' in e]
        if len(toggles)==1:
            return 'Bluetooth '+('включён.' if toggles[0]['toggle_state']==1 else 'выключен.')
        return 'Открыта страница Bluetooth. Состояние переключателя не удалось подтвердить.'
    return {'notepad':'Блокнот открыт.','settings':'Настройки Windows открыты.',
            'focus_notepad':'Блокнот найден на виртуальном рабочем столе и активирован.',
            'write':'В Блокноте написано: '+str(expected)}[kind]
