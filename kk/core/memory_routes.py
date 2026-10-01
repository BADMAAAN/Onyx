"""Small explicit memory commands; no desktop observation or speculative recall."""
import re
from .task import TaskStatus


def route(text, task, tools):
    folded = text.casefold().strip(' .!?')
    if re.match(r'^(?:забудь|удали|очисти)\s+(?:всё|все|всю)\b', folded):
        return ('Forget поддерживает точный ключ сохранённого факта: «Забудь предпочтение key». '
                'Удаляются этот факт, его поисковый индекс и связанное application preference; '
                'сбрасывается текущий контекст решений. История разговоров/действий, ambient-эпизоды, '
                'навыки, алиасы и backups сохраняются. Широкий запрос ничего не удалил; укажите ключ.')
    save = re.fullmatch(r'(?:запомни|сохрани|исправь)\s+(?:предпочтение|настройку)\s+'
                       r'(?:с ключом\s+)?([\w.-]+)\s*:\s*(.+)', text, flags=re.I)
    forget = re.fullmatch(r'(?:забудь|удали)\s+(?:предпочтение|настройку|факт)\s+'
                         r'(?:с ключом\s+)?([\w.-]+)', text, flags=re.I)
    if save or forget:
        name = 'remember_preference' if save else 'forget_memory'
        if name not in getattr(tools, '_tools', {}):
            return None
        args = {'key': save[1], 'value': save[2]} if save else {'key': forget[1]}
        task.before_action(name, args, {})
        result = tools.execute(name, args).as_dict()
        task.actions[-1]['result'] = result
        if not result['ok']:
            task.status = TaskStatus.FAILED
            return 'Память не изменена: ' + str(result['error'])
        if save:
            return 'Сохранено предпочтение ' + result['data']['saved'] + '.'
        return ('Удалён сохранённый факт и его индекс.' if result['data']['forgotten'] else
                'Такого сохранённого факта нет.') + ' История разговоров/действий, эпизоды и backups сохранены.'
    if re.match(r'^что я\b', folded) and re.search(r'нажимал|нажал|выбирал|выбрал|клик', folded):
        ambient = getattr(tools, 'ambient', None)
        if ambient is None:
            return 'Нет доступного источника семантических действий; активное окно не доказывает нажатие.'
        evidence = ambient.retrieve_for_query(text) or {}
        episodes = evidence.get('summary', {}).get('user_ui_actions', [])
        # An explicitly named fixture/window must not retrieve another app.
        target = re.search(r'\bв\s+(.+?)(?:[?.!]|$)', text, re.I)
        if target:
            label = target[1].casefold()
            episodes = [e for e in episodes if label in
                        (e['window'].get('title', '') + ' ' + e['window'].get('process', '')).casefold()]
        if not episodes:
            return 'В истории нет подтверждённого семантического события. Фокус или ввод не доказывают нажатие конкретной кнопки.'
        from datetime import datetime, timezone
        lines = []
        for episode in episodes[-5:]:
            details = episode['details']
            stamp = datetime.fromtimestamp(details.get('observed_at', episode['timestamp']), timezone.utc).isoformat()
            attribution = 'тестовая автоматизация' if details.get('source') == 'test_automation' else 'автор действия установлен предположительно'
            reference = episode.get('source_ref') or f"ambient:event:{episode['timestamp']}"
            lines.append(f"{details['operation']}: «{details['name']}», {episode['window'].get('title') or episode['window'].get('process')}; "
                         f"{stamp}; источник {reference}, UIA {details.get('uia_event_id')}; {attribution}.")
        return '\n'.join(lines)
    return None
