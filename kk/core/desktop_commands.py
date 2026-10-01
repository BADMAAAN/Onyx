"""Deterministic desktop plans, preserving literal text and per-step evidence."""
from dataclasses import dataclass
import re
from time import monotonic, perf_counter
from .numbers import spoken_number
from .task import TaskStatus
from .state import AgentState
import logging


from .decision_policy import OperationalContext

DesktopContext = OperationalContext


def _public_failure(error):
    # Only authored explanations may enter chat/TTS. Tool/OS exceptions stay
    # in the action journal and the task-scoped log, including paths and URLs.
    known = {
        'not found': 'Приложение не найдено.',
        'missing': 'Приложение не найдено.',
        'Окно не подтверждено': 'Открытие окна не подтверждено.',
        'Состояние окна не подтверждено': 'Изменение окна не подтверждено.',
        'Страница в браузере не подтверждена': 'Страница в браузере не подтверждена.',
        'Открытие цели не подтверждено': 'Открытие не подтверждено.',
        'Закрытие не подтверждено; возможен диалог несохранённых изменений': 'Закрытие не подтверждено. Возможно, нужно сохранить изменения.',
        'Текст поиска не подтверждён': 'Ввод текста не подтверждён.',
        'Запуск не подтверждён': 'Запуск не подтверждён.',
        'Громкость не подтверждена': 'Изменение громкости не подтверждено.',
        'Монитор не подтверждён': 'Перемещение окна не подтверждено.',
    }
    return known.get(str(error), 'Не удалось выполнить действие. Подробности — в журнале Onyx.')


def monitor_suffix(text):
    english = re.search(r'(?i)\s+on\s+(?:the\s+)?(first|second|\d+)(?:\s+(?:monitor|screen))$', text)
    if english:
        number = {'first': 1, 'second': 2}.get(english[1].casefold())
        return text[:english.start()].strip(), number or int(english[1])
    match = re.search(r'\s+на\s+(первом|первый|втором|второй|\d+)(?:\s+(?:экране?|мониторе?))?$', text, re.I)
    if not match: return text, None
    value = match[1].casefold()
    index = 1 if value.startswith('перв') else 2 if value.startswith('втор') else int(value)
    return text[:match.start()].strip(), index


def parse_plan(text, context=None, intents=None):
    from .personal_intents import DEFAULT_INTENTS, corrected
    from .fast_commands import normalize
    intents = intents or DEFAULT_INTENTS
    command = normalize(text)
    from .command_language import has_unquoted_negation, polite_request
    polite = polite_request(command)
    if polite != command:
        command = text = polite
    if has_unquoted_negation(command):
        return None
    learning = intents.learning(command)
    if learning: return [learning]
    game_correction = re.fullmatch(r'(?:Нет,?\s+)?(.+?)\s+запускай через\s+(.+)', command, re.I)
    if game_correction:
        return [dict(kind='game_preference', game=game_correction[1], launcher=game_correction[2])]
    game = re.fullmatch(r'(?:хочу поиграть в|давай поиграем в|играть в)\s+(.+)', command, re.I)
    if game: return [dict(kind='game', game=game[1])]
    if re.match(r'(?i)^зайди на\s+', command):
        target = re.sub(r'(?i)^зайди на\s+', '', command)
        entry = intents.resolve(target)
        if entry and entry['kind'] == 'url': return [dict(kind='url', url=entry['target'], monitor=None)]
    if not re.search(r'\b(?:напиши|введи|найди)\b', command, re.I):
        command = corrected(command)
        command = re.sub(r'^(?:теперь|а)\s+', '', command, flags=re.I)
        standalone = re.sub(r'\s+в браузере$', '', command, flags=re.I)
        names = re.split(r'\s+и\s+|,\s*|\s+and\s+', standalone, flags=re.I)
        if intents.resolve(standalone) or (len(names)>1 and all(intents.resolve(n) for n in names)):
            command = 'открой ' + standalone
        text = command
    plan = _parse_plan(text, context, intents)
    if not plan: return plan
    expanded = []
    for step in plan:
        if step['kind'] in {'open', 'window', 'move'}:
            name = re.sub(r'\s+в браузере$', '', step['app'], flags=re.I)
            entry = intents.resolve(name)
            if entry:
                if entry['kind'] == 'app': step['app'] = entry['target']
                elif entry['kind'] == 'url' and step['kind']=='open': step = dict(kind='url', url=entry['target'], monitor=step.get('monitor'))
                elif entry['kind'] == 'skill': step = dict(kind='skill', name=entry['target'])
                elif entry['kind'] == 'task':
                    expanded.extend(dict(s) for s in entry['target'])
                    continue
        expanded.append(step)
    return expanded


def _parse_plan(text, context=None, intents=None):
    from .fast_commands import normalize
    command = normalize(text)
    literal = re.sub(r'^(?:оникс|onyx|кей[\s-]+кей|окей[\s-]+окей|kk)\b[ ,.!?-]*', '', text.strip(), flags=re.I)
    # Politeness is syntax around a target, never a replacement inside payload.
    command = re.sub(r'(?i)(?:^|[,\s]+)(?:пожалуйста|please)(?:[,\s]+|$)', ' ', command).strip(' ,')
    command = re.sub(r'(?i)^(открой|открыть|запусти|закрой)\s+мне\s+', r'\1 ', command)
    suffix = re.fullmatch(r'(?is)(.+?)\s+(открой|открыть|запусти)', command)
    if suffix and not re.search(r'(?i)\b(?:напиши|введи|выполни|не|нет|хотя)\b', suffix[1]):
        command = suffix[2] + ' ' + suffix[1]
    app_context = context.recent() if context else ''
    from .window_intents import window_intent
    window = window_intent(command, app_context)
    if window: return [window]
    from .command_language import typed_open
    explicit_type, typed = typed_open(command)
    if explicit_type: return [typed] if typed else None
    if re.fullmatch(r'(?i)(?:открой|открыть)\s+(?:корзину|мусорку)', command):
        return [dict(kind='target', name='Recycle Bin', type='virtual_shell_item', location='')]
    alias = re.fullmatch(r'запомни[:,]?\s+(.+?)\s+(?:это|—|=)\s+(.+)', command, re.I)
    if alias and not re.match(r'(?i)(?:предпочтение|произношение|факт|что)\b', alias[1]):
        return [{'kind': 'alias', 'spoken': alias[1], 'application': alias[2]}]
    # Capture payload before any splitting, case folding or punctuation removal.
    search = re.fullmatch(r'(?:в\s+(.+?)\s+)?(?:открой\s+поиск\s+и\s+)?(напиши|введи|найди)\s+(.+)', literal, re.I)
    if search and (search[1] or re.search(r'открой\s+поиск', literal, re.I)):
        app = search[1] or app_context
        return [{'kind': 'search', 'app': app, 'text': search[3], 'submit': search[2].casefold() == 'найди'}]
    clauses = re.split(r'(?:,\s*|\s+и\s+)(?=(?:открой|запусти|перенеси|перемести|разверни|сверни|восстанови|(?:сделай|поставь|установи)\s+громкость|громкость)\b)', literal, flags=re.I)
    if len(clauses) > 1:
        plans = [parse_plan(clause, context, intents) for clause in clauses]
        return [step for plan in plans for step in plan] if all(plans) else None
    volume = re.fullmatch(r'(?:(?:сделай|поставь|установи|set)\s+)?(?:громкость|volume)\s*(?:на\s+|to\s+)?(.+)', command, re.I)
    if volume:
        value = spoken_number(volume[1])
        return [{'kind': 'volume', 'level': value, 'valid': value is not None and 0 <= value <= 100}]
    move = re.fullmatch(r'(?:перенеси|перемести)\s+(.+)', command, re.I)
    if move:
        name, monitor = monitor_suffix(' '+move[1])
        if monitor:
            referent = name.casefold() in {'', 'его', 'ее', 'её', 'это', 'окно', 'туда'}
            if referent: name = app_context
            return [{'kind': 'move', 'app': name, 'monitor': monitor, 'referent': referent}]
    if not re.match(r'^(?:открой|открыть|запусти|open|launch)\s+', command, re.I): return None
    if re.search(r'\b(?:первый|последний|текущий)\s+чат\b', command, re.I): return None
    # Composite natural tasks belong to the agent, never an invented app name.
    if re.search(r'\b(?:зайди|найди|включи|последний|ролик|видео|скачай|поищи|перейди)\b', command, re.I): return None
    if re.search(r'\b(?:напиши|введи|вставь|выполни|найди|type|write|execute|send|настройки|bluetooth|нет|хотя|не надо)\b', command, re.I): return None
    body = re.sub(r'^(?:открой|открыть|запусти|open|launch)\s+', '', command, flags=re.I)
    parts = re.split(r'\s*,\s*|\s+и\s+|\s+and\s+', body, flags=re.I)
    plan = []
    for part in parts:
        window = window_intent(part)
        if window:
            plan.append(window)
            continue
        part = re.sub(r'^(?:открой|запусти|open|launch)\s+', '', part, flags=re.I)
        name, monitor = monitor_suffix(part)
        full = re.search(r'\s+на\s+(?:full|полный(?: экран)?|весь экран)$', name, re.I)
        maximize = bool(full)
        if full: name = name[:full.start()].strip()
        if not name or re.search(r'\b(?:скажи|прочитай|удали|напиши|перенеси)\b', name, re.I): return None
        plan.append({'kind': 'open', 'app': name, 'monitor': monitor})
        if maximize: plan.append(dict(kind='window', operation='maximize', app=''))
    return plan


def run_desktop(text, task, tools, state, context, plan=None):
    from .personal_intents import PersonalIntentRegistry, DEFAULT_INTENTS
    from kk.tools.windows.app_resolver import TargetClarificationRequired
    intents = getattr(tools, 'personal_intents', None)
    if not isinstance(intents, PersonalIntentRegistry): intents = DEFAULT_INTENTS
    if plan is None:
        plan = parse_plan(text, context, intents)
    if plan is None: return None
    task.metrics.update(brain_route='deterministic_no_llm', brain_first_token_seconds=None, desktop_plan=plan)
    task.cancellation_token.mark('intent_decided', route='deterministic_no_llm')
    answers, failed = [], False
    from .task_batch import TaskBatch
    batch = TaskBatch.from_plan(text, plan)
    batch.constraints = task.request_scope
    task.batch = batch
    batch.status = 'running'
    def execute(name, args):
        task.before_action(name, args, {})
        state.set(AgentState.ACTING, name)
        started=perf_counter()
        task.cancellation_token.mark('tool_start', tool=name, arguments=args)
        result = tools.execute(name, args).as_dict()
        task.cancellation_token.mark('tool_end', tool=name, ok=result['ok'], result=result)
        task.actions[-1]['execution_seconds']=perf_counter()-started
        task.actions[-1]['result'] = result
        task.check()
        if isinstance(result.get('data'),dict) and result['data'].get('status') == 'needs_clarification':
            raise TargetClarificationRequired(result['data']['question'])
        if not result['ok']:
            error = re.sub(r'^[A-Za-z]+(?:Error|Exception):\s*', '', result['error'] or 'Не удалось выполнить действие')
            raise RuntimeError(error)
        return result['data']
    for item in batch.tasks:
        step = item.spec
        try: task.check()
        except (InterruptedError, TimeoutError):
            batch.status = 'cancelled'
            for pending in batch.tasks:
                if pending.status == 'pending': pending.status = 'cancelled'
            raise
        if any(t.status != 'completed' for t in batch.tasks if t.id in item.dependencies):
            item.status = 'skipped'
            item.result = 'dependency_failed'
            failed = True
            continue
        item.status = 'running'
        answer_start = len(answers)
        try:
            if step['kind'] == 'target':
                data = execute('open_desktop_target', {key:step[key] for key in ('name','type','location')})
                task.metrics['resolved_target'] = data.get('target')
                task.cancellation_token.mark('target_resolved', target=data.get('target'), result=data)
                if not data.get('verified'): raise RuntimeError(data.get('reason') or 'Открытие цели не подтверждено')
                target = data.get('target') or {}
                answers.append('Открыла ' + (target.get('name') or data.get('name') or step['name']) + '.')
                continue
            if step['kind'] == 'window':
                target=step.get('app')
                if step['operation'] == 'close' and target:
                    data = execute('close_application', {'query':target, 'scope':step.get('scope','application')})
                    task.metrics['resolved_target'] = data.get('target')
                    if not data.get('verified'): raise RuntimeError(data.get('reason') or 'Закрытие не подтверждено; возможен диалог несохранённых изменений')
                    answers.append('Закрыла ' + (data.get('name') or target) + '.')
                    continue
                hwnd=context.hwnd if context.recent() and (not target or target==context.app) else None
                if target and hwnd is None:
                    opened=execute('open_application',{'name':target}); hwnd=opened.get('hwnd')
                    if not hwnd or not opened.get('verified'): raise RuntimeError('Целевое окно не подтверждено')
                    context.app, context.hwnd, context.updated = target, hwnd, monotonic()
                    context.process_id = opened.get('process_id',0)
                data=execute(step['operation']+'_window' if step['operation']!='fullscreen' else 'toggle_fullscreen', {'hwnd':hwnd} if hwnd else {})
                if not data.get('verified'): raise RuntimeError('Состояние окна не подтверждено')
                item.target_window = data.get('hwnd',hwnd)
                answers.append({'maximize':'Развернула.','restore':'Восстановила.','minimize':'Свернула.','close':'Закрыла.','fullscreen':'Готово.'}[step['operation']])
                continue
            if step['kind'] == 'personal_alias':
                data = execute('remember_personal_intent', dict(phrase=step['phrase'], **step['entry']))
                answers.append(f"Запомнил: {data['alias']} — {data['application']}.")
                continue
            if step['kind'] in {'game', 'game_preference', 'skill'}:
                if step['kind'] == 'game_preference':
                    execute('remember_game_launcher', dict(game=step['game'], launcher=step['launcher']))
                    answers.append('Способ запуска сохранён.')
                else:
                    name = 'launch_game' if step['kind'] == 'game' else 'run_personal_skill'
                    data = execute(name, {'game': step['game']} if step['kind'] == 'game' else {'name': step['name']})
                    if not data.get('verified'): raise RuntimeError('Запуск не подтверждён')
                    answers.append((step.get('game') or step.get('name')) + ' запущен.')
                continue
            if step['kind'] == 'url':
                args = {'url': step['url']}
                if context.recent() and context.browser: args['browser'] = context.browser
                if step.get('monitor'): args['monitor_index'] = step['monitor']
                data = execute('open_url', args)
                if not data.get('verified'): raise RuntimeError('Страница в браузере не подтверждена')
                answers.append('Открыла ' + step['url'] + '.')
                continue
            if step['kind'] == 'alias':
                data = execute('remember_app_alias', {'spoken': step['spoken'], 'application': step['application']})
                answers.append(f"Запомнил: {data['alias']} — {data['application']}.")
                continue
            if step['kind'] == 'volume':
                if not step['valid']:
                    item.status = 'failed'
                    answers.append('Какую громкость поставить — от 0 до 100 процентов?')
                    failed = True
                    continue
                execute('set_volume', {'level': step['level']})
                actual = execute('get_volume', {})
                if abs(actual['percent'] - step['level']) > 1: raise RuntimeError('Громкость не подтверждена')
                answers.append(f"Громкость {step['level']} процентов.")
                continue
            app = step['app']
            if app.casefold() in {'браузер', 'browser'} and context.recent() and context.browser:
                app = context.browser
            if step.get('referent'): app = context.recent()
            if not app and step['kind'] in {'search', 'move'}:
                app = context.recent()
            if step.get('monitor') is not None and step['monitor'] < 1:
                item.status = 'failed'
                failed = True
                answers.append('На какой экран перенести окно? Нумерация начинается с первого.')
                continue
            if not app and step['kind'] == 'move':
                window = execute('get_foreground_window', {})
                app = window.get('process') or window.get('title')
                context.app, context.hwnd, context.updated = app or '', window.get('hwnd', 0), monotonic()
            if not app:
                item.status = 'failed'
                answers.append('Какое приложение открыть?')
                failed = True
                continue
            if step['kind'] == 'search':
                data = execute('search_and_type_in_app', {'app': app, 'text': step['text'], 'submit': step['submit']})
                if not data.get('verified'): raise RuntimeError(data.get('reason') or 'Текст поиска не подтверждён')
                answers.append(('Запрос отправлен в поиск: ' if step['submit'] else 'В поиске написано: ') + step['text'])
                context.app, context.hwnd, context.updated = app, data['hwnd'], monotonic()
                context.process_id = data.get('process_id', 0)
                context.last_window = data.get('window')
                context.last_monitor = data.get('monitor_index')
                continue
            if step['kind'] == 'place':
                parent = next(t for t in batch.tasks if t.id in item.dependencies)
                data = {'hwnd': parent.target_window, 'verified': bool(parent.target_window)}
            elif step['kind'] == 'move' and context.recent() == app and context.hwnd:
                data = {'hwnd': context.hwnd, 'verified': True, 'process_id': context.process_id,
                        'monitor_index': context.last_monitor, 'window': context.last_window}
            else:
                data = execute('open_application', {'name': app})
            if not data.get('verified') or not data.get('hwnd'): raise RuntimeError('Окно не подтверждено')
            item.target_window = data['hwnd']
            if step.get('monitor'):
                moved = execute('move_window_handle_to_monitor', {'hwnd': data['hwnd'], 'monitor_index': step['monitor']})
                if moved['after']['monitor_index'] != step['monitor']: raise RuntimeError('Монитор не подтверждён')
                data.update(moved['after'])
                data['window'] = moved['after']
            context.app, context.hwnd, context.updated = app, data['hwnd'], monotonic()
            context.process_id = data.get('process_id',0)
            context.last_monitor = step.get('monitor') or data.get('monitor_index')
            context.last_window = data.get('window')
            context.last_entity, context.last_action = app, step['kind']
            from kk.tools.windows.app_resolver import canonical
            if canonical(app) in {'browser','firefox','chrome','msedge','brave'}: context.browser = app
            # Decline only known public labels; arbitrary executable/app names
            # are proper names and must not be changed by guessed grammar.
            label = {'yandexmusic':'Яндекс Музыку', 'telegram':'Telegram',
                     'discord':'Discord', 'firefox':'Firefox', 'obsidian':'Obsidian', 'vscode':'VS Code'}.get(canonical(app), app)
            if canonical(app) == 'telegram':
                from pathlib import PureWindowsPath
                window = data.get('window') or {}
                executable = data.get('executable') or window.get('executable') or window.get('process') or ''
                label = {'ayugram':'AyuGram', 'telegram':'Telegram', 'spacegram':'SpaceGram'}.get(
                    PureWindowsPath(executable).stem.casefold(), label)
            answers.append(app + f" на экране {step['monitor']}." if step.get('monitor') else f'Открыла {label}.')
        except TargetClarificationRequired as exc:
            task.status = TaskStatus.WAITING
            item.status = batch.status = 'waiting'
            batch.result = exc.question
            answers.append(exc.question)
            return exc.question
        except (InterruptedError, TimeoutError):
            item.status = batch.status = 'cancelled'
            for pending in batch.tasks:
                if pending.status == 'pending': pending.status = 'cancelled'
            raise
        except RuntimeError as exc:
            item.status = 'failed'
            failed = True
            logging.getLogger('kk').exception('[DESKTOP ACTION] task_id=%s step=%s', task.id, step['kind'])
            answers.append(f"{step.get('app') or 'Действие'}: {_public_failure(exc)}")
        finally:
            if item.status == 'running': item.status = 'completed'
            item.result = ' '.join(answers[answer_start:])
    if failed: task.status = TaskStatus.FAILED
    batch.status = 'failed' if failed else 'completed'
    batch.result = ' '.join(answers)
    return batch.result
