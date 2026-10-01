"""Collection questions require collection evidence, including its coverage."""
import re
from .task import TaskStatus


def route_collection(command, task, tools):
    folded = command.casefold().strip(' .!?')
    tabs = re.fullmatch(r'(?:какие|что за) (?:все )?вкладки (?:сейчас )?открыты(?: (?:во всех|в двух) окнах)?', folded) or re.fullmatch(
        r'(?:покажи|перечисли|список)(?: мне)? (?:все |открытые )?вкладки(?: (?:во всех|в двух) окнах)?', folded)
    explorer = folded in {'какие папки открыты', 'покажи открытые папки',
        'открой папки, которые уже открыты', 'открой папки которые уже открыты'}
    if not tabs and not explorer:
        return None
    if tabs:
        name = 'list_browser_tabs'
        args = {'scope':'all_windows' if re.search(r'\b(?:все|всех|двух)\b', folded) else 'current_window'}
    else:
        name = 'show_explorer_locations' if folded.startswith(('покажи','открой')) else 'list_explorer_locations'
        args = {}
    task.metrics.update(brain_route='collection_no_llm', collection_tool=name)
    task.cancellation_token.mark('intent_decided', route='collection_no_llm', intent=name)
    task.before_action(name, args, {})
    task.cancellation_token.mark('tool_start', tool=name, arguments=args)
    result = tools.execute(name, args)
    task.actions[-1]['result'] = result.as_dict()
    task.cancellation_token.mark('tool_end', tool=name, ok=result.ok, result=result.as_dict())
    task.check()
    if not result.ok:
        task.status = TaskStatus.FAILED
        return 'Не удалось получить полный список: ' + str(result.error or 'источник недоступен')
    data = result.data or {}
    coverage = data.get('coverage') or data
    items = data.get('items') or coverage.get('items') or []
    labels = []
    for item in items:
        label = item.get('title') if tabs else item.get('path') or item.get('location') or item.get('title')
        label = label or 'без названия'
        suffix = ' (активная)' if tabs and item.get('active') else ''
        labels.append(f'{len(labels)+1}. {label}{suffix}')
    complete = coverage.get('completeness') == 'complete'
    observation_status = coverage.get('status') or data.get('status')
    task.metrics['collection_observation'] = {
        'status': observation_status or ('VERIFIED' if complete else 'UNVERIFIED'),
        'completeness': coverage.get('completeness', 'unknown'),
        'source': coverage.get('source'), 'scope': coverage.get('scope'),
        'timestamp': coverage.get('timestamp')}
    if observation_status == 'BLOCKED':
        task.status = TaskStatus.BLOCKED
        complete = False
    elif not complete or observation_status == 'UNVERIFIED':
        task.status = TaskStatus.UNVERIFIED
        complete = False
    task.actions[-1]['verification'] = {'verified': complete,
        'status': task.metrics['collection_observation']['status']}
    if complete:
        prefix = (('Вкладки всех доступных окон' if args.get('scope') == 'all_windows' else 'Вкладки текущего окна') if tabs else 'Открытые папки') + ': '
        return prefix + ('; '.join(labels) + '.' if labels else 'в указанной области не найдены.')
    reasons = coverage.get('limitations') or coverage.get('reasons') or coverage.get('reason') or data.get('reason') or 'источник не подтвердил полноту наблюдения'
    if isinstance(reasons, list): reasons = '; '.join(str(reason) for reason in reasons)
    return ('Доступная часть списка: ' + '; '.join(labels) + '. ' if labels else '') + 'Полный список не подтверждён: ' + str(reasons) + '.'
