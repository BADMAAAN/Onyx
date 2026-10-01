"""Natural context queries and small media tasks, before the general planner."""
import re
from time import monotonic
from .task import TaskStatus
from .decision_policy import OperationalContext


def screen_related(text):
    if re.search(r'(?i)^(?:где|на каком экране|что сейчас играет|какая у меня|сколько у меня|что я делал)\b',text): return True
    return bool(re.search(r'(?i)экран|монитор|окн[оеа]|картин|изображен|изображён|что там|это за ошибк|этот код|этом код|тут ошибк', text))


def requests_context_read(text):
    """Reading current local context needs tools even without an action verb."""
    folded = text.casefold().strip(' .!?')
    return screen_related(folded) or bool(re.search(r'\b(?:downloads|загрузк\w*)\b', folded))


def visual_inspection(text):
    folded = text.casefold().strip(' .!?')
    if folded in {'что на экране','что сейчас на экране'}: return True
    return bool((re.search(r'экран|монитор', folded) or re.search(r'на (?:первом|втором)\b', folded)) and
                re.match(r'^(?:посмотри|глянь|взгляни|опиши|что (?:ты )?(?:сейчас )?(?:видишь|видно))\b', folded)
                and not re.search(r'\b(?:нажми|открой|введи|напиши|включи|перенеси|удали)\b', folded))


def route(text, task, tools, state):
    folded = text.casefold()
    monitor = re.fullmatch(r'что (?:сейчас )?(?:находится |открыто )?на (первом|втором|\d+)(?: (?:экране|мониторе))?', folded)
    inspection = visual_inspection(folded)
    downloads = bool(re.fullmatch(r'(?:посмотри|покажи|список|что (?:есть |лежит )?в)(?: папк[еу])?\s+(?:моих |мои )?(?:загрузк[иах]*|downloads)|(?:загрузки|downloads)[, ]+посмотри', folded))
    downloads = downloads and 'list_downloads' in getattr(tools, '_tools', {})
    resume_video = folded in {'продолжи фильм','продолжи видео','возобнови фильм','возобнови видео','вернись к фильму и продолжи'}
    media = folded in {'включи музыку','музыку включи','play music'}
    latest_music = folded in {'включи последний добавленный трек',
        'открой яндекс музыку и включи последний добавленный трек'}
    latest_music = latest_music and ('play_latest_added_yandex' in getattr(tools, '_tools', {}))
    telegram = {'открой первый чат':'open_first_telegram_chat','начни записывать голосовое':'start_voice_recording',
                'начни запись голосового':'start_voice_recording','отмени голосовое':'finish_voice_recording'}.get(folded)
    if re.fullmatch(r'открой первый чат(?: (?:в )?(?:telegram|телеграм|телеграме|ayugram))?',folded):
        telegram='open_first_telegram_chat'
    if folded in {'отправь','отправь голосовое'} and getattr(getattr(tools,'telegram',None),'recording_hwnd',None):
        telegram='finish_voice_recording'
    if folded in {'отмени','отмена','отмени запись','отмени голосовое'} and getattr(getattr(tools,'telegram',None),'recording_hwnd',None):
        telegram='finish_voice_recording'
    visual = folded in {'что там открыто','что это за ошибка','почему тут ошибка','что на картинке','что на изображении'}
    if not (monitor or inspection or downloads or resume_video or media or latest_music or visual or telegram): return None
    context = getattr(tools, 'desktop_context', None)
    if not isinstance(context, OperationalContext):
        context = OperationalContext()
        tools.desktop_context = context
    task.metrics.update(brain_route='deterministic_no_llm', brain_first_token_seconds=None)
    task.cancellation_token.mark('intent_decided', route='deterministic_no_llm')

    def call(name, **args):
        from .state import AgentState
        task.before_action(name, args, {})
        state.set(AgentState.ACTING, name)
        result = tools.execute(name, args).as_dict()
        task.actions[-1]['result'] = result
        task.check()
        if not result['ok']: raise RuntimeError(result.get('error') or 'Нет результата')
        data = result['data']
        if name == 'list_windows' and isinstance(data, dict):
            return data.get('items', [])
        return data

    try:
        if downloads:
            data = call('list_downloads')
            entries = data.get('entries', [])
            complete = data.get('completeness') == 'complete'
            if not entries:
                return 'Папка Загрузки пуста.' if complete else 'Элементы Загрузок не получены; полнота наблюдения не подтверждена.'
            names = [entry['name'] + (' (папка)' if entry.get('type') == 'directory' else '') for entry in entries[:10]]
            prefix = f"В Загрузках {len(entries)} элементов: " if complete else 'Доступная часть Загрузок: '
            return prefix + '; '.join(names) + ('.' if len(entries) <= 10 else '; и другие.') + ('' if complete else ' Полнота списка не подтверждена.')
        if resume_video:
            windows = call('list_windows')
            sessions = call('get_media_sessions')
            matches = [w for w in windows if w.get('process','').casefold() in {'firefox.exe','chrome.exe','msedge.exe','brave.exe','opera.exe'}
                       and any(w['process'].removesuffix('.exe').casefold() in (s.get('session_id','')+' '+s.get('process','')).casefold()
                               and len(s.get('title','')) >= 3 and s['title'].casefold() in w.get('title','').casefold() for s in sessions)]
            if len(matches) != 1:
                task.metrics['context_needs_planner'] = True
                return None
            call('focus_window', hwnd=matches[0]['hwnd'])
            data = call('browser_control', action='resume_video')
            if data.get('verified'): return 'Продолжила воспроизведение.'
            task.status = TaskStatus.FAILED
            return data.get('reason') or 'Воспроизведение в существующей вкладке не подтвердилось.'
        if inspection:
            match = re.search(r'(перв(?:ом|ый)|втор(?:ом|ой)|\d+)\s+(?:экран|монитор)|(?:экран|монитор)(?:е)?\s+(\d+)|на (первом|втором)\b', folded)
            if match:
                value = next(value for value in match.groups() if value is not None)
                index = 1 if value.startswith('перв') else 2 if value.startswith('втор') else int(value)
                data = call('see_monitor', monitor_index=index, question=text, mode='vision')
            else:
                target = 'all' if re.search(r'обоих|оба|всех|все', folded) else 'active_monitor'
                data = call('see_screen', target=target, question=text, mode='vision')
            if not isinstance(data, dict) or not data.get('description'):
                task.status = TaskStatus.FAILED
                return 'Свежий кадр не удалось описать.'
            return data['description']
        if latest_music:
            data=call('play_latest_added_yandex')
            if data.get('verified') and data.get('ordering')=='newest_first' and data.get('media_session',{}).get('playing'):
                return 'Включила последний добавленный трек.'
            task.metrics['brain_route']='latest_music_needs_ui'
            return None
        if telegram:
            args={'send':folded.startswith('отправь')} if telegram=='finish_voice_recording' else {}
            data=call(telegram,**args)
            if not data.get('verified'):
                if telegram=='start_voice_recording':
                    task.status=TaskStatus.FAILED
                    return 'Начало записи не удалось подтвердить. Ничего не отправляла.'
                if telegram=='finish_voice_recording':
                    task.status=TaskStatus.FAILED
                    return 'Не удалось подтвердить отправку голосового.' if args['send'] else 'Не удалось подтвердить отмену записи.'
                task.metrics['context_needs_planner']=True
                return None
            if telegram=='finish_voice_recording':
                return 'Голосовое отправлено.' if args['send'] else 'Запись отменена.'
            return {'open_first_telegram_chat':'Открыла первый чат.', 'start_voice_recording':'Записываю.'}[telegram]
        if monitor:
            value = monitor[1]
            index = {'первом':1, 'втором':2}.get(value) or int(value)
            monitors = call('get_monitors')
            if index not in {m['index'] for m in monitors}:
                return f'Экран {index} сейчас не подключён.'
            context.last_monitor = index
            windows = call('list_windows')
            rows = [w for w in windows if w.get('monitor_index') == index and not w.get('minimized')
                    and w.get('title') not in {'Onyx Orb', 'Onyx Edge Presence', 'KK Orb', 'KK Edge Presence', 'Program Manager', 'Интерфейс ввода Windows'}]
            if rows:
                return f'На экране {index}: ' + '; '.join(dict.fromkeys(w.get('title') or w.get('process') or 'окно без заголовка' for w in rows[:8])) + '.'
            data = call('see_monitor', monitor_index=index, question=text)
            return f'Экран {index}: ' + (data.get('description') or 'Не удалось прочитать содержимое экрана.')
        if visual:
            args = dict(question=text, mode='vision' if 'картин' in folded or 'изображен' in folded else 'auto')
            data = call('see_screen', **args)
            if 'ошибк' in folded or folded=='что там открыто':
                task.metrics['context_needs_planner']=True
                return None
            return data.get('description') or 'Не удалось прочитать экран: восприятие не вернуло описания.'
        if media:
            data = call('play_music')
            if data.get('verified'):
                return 'Включила.'
            # Preserve evidence for the planner to inspect the player UI and replan.
            task.metrics['brain_route'] = 'media_needs_ui'
            return None
    except RuntimeError as exc:
        if telegram:
            task.status=TaskStatus.FAILED
            return 'Telegram: '+str(exc)
        if media or latest_music or resume_video:
            task.metrics['brain_route'] = 'media_needs_ui'
            return None
        task.status = TaskStatus.FAILED
        if downloads: return 'Не удалось получить список Загрузок: ' + str(exc)
        return 'Не удалось прочитать экран: ' + str(exc)
