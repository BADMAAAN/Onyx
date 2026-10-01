"""Small, inspectable routing decisions; models come from measured configuration."""
from dataclasses import dataclass
import re


def character_budget(value):
    """Normalize only lossless numeric counts; reject malformed boundaries."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if type(value) is not int:
        raise TypeError('context budget must be an integer character count')
    if value <= 0:
        raise ValueError('context budget must be positive')
    return value


@dataclass(frozen=True)
class BrainChoice:
    tier: str
    model: str
    reason: str


class BrainRouter:
    def __init__(self, fast, deep=None):
        self.fast, self.deep = fast, deep or fast

    def choose(self, request, retries=0):
        complex_goal = bool(re.search(
            r'разберись|исправь|почему.*(?:ошиб|сбор|пада|не работа)|'
            r'проект.*(?:собира|тест)|debug|diagnos|refactor|implement', request, re.I))
        steps = len(re.findall(r'\b(?:открой|найди|включи|перенеси|запусти|прочитай|open|find|run)\b', request, re.I))
        deep = complex_goal or steps >= 3 or retries > 0
        return BrainChoice('deep' if deep else 'fast', self.deep if deep else self.fast,
                           'replan' if retries else 'complex_goal' if deep else 'conversation_or_simple_goal')


class ToolCapabilityRouter:
    """Advertising is scoped; authorization remains in ToolRegistry.execute."""
    CATEGORIES = {
        'apps': (r'открой|приложен|запусти|open|launch|\btg\b|\bds\b', r'application|app_in_context|desktop_target|obsidian_draft'),
        'windows': (r'окн|разверни|сверни|перенеси|слева|справа|назад|window', r'window|fullscreen'),
        'monitor': (r'экран|монитор|monitor|screen', r'monitor|desktop_context|list_windows'),
        'browser': (r'браузер|firefox|chrome|youtube|сайт|вкладк|видео|chatgpt|\bgpt\b|browser|website', r'browser|url|media'),
        'media': (r'музык|трек|песн|пауз|громк|music|track|playback', r'media|music|track|audio|volume|latest_added_yandex'),
        'ui': (r'напиши|введи|вставь|чат|голосов|telegram|телег|ayugram|кнопк|нажми|scroll|type|click', r'ui_|type_text|hotkey|mouse|scroll|telegram|voice_recording|text_entry'),
        'vision': (r'посмотри|видишь|картин|изображ|на экран|на первом|на втором|ошибк|vision', r'see_|screen|ui_tree'),
        'filesystem': (r'файл|папк|каталог|скачан|pdf|file|folder|directory|\.(?:txt|py|pdf|md|json|toml|csv|docx)\b', r'file|directory|folder|desktop_target|explorer_locations|^copy$|^move$|^rename$|^delete$'),
        'terminal': (r'powershell|терминал|команд|выполни|terminal|ipconfig|ifconfig', r'terminal|text_entry|type_text|hotkey|application'),
        'development': (r'проект|код|тест|сборк|build|git|codex|debug|tests', r'git|terminal|codex|file|directory'),
        'memory': (r'помни|запомни|забудь|раньше|предпоч|memory|remember|forget', r'memor|preference|intent|alias|skill'),
        'system': (r'настройк|систем|bluetooth|сеть|settings|network|system', r'settings|system|network|toggle'),
    }
    COMMON = {'discover_tools', 'observe_context', 'get_desktop_context', 'recall_memories'}
    PREFERRED = {
        'apps': ['resolve_desktop_target', 'open_desktop_target', 'open_application', 'get_monitors', 'list_windows'],
        'windows': ['maximize_window', 'move_window_to_monitor', 'focus_window'],
        'monitor': ['get_monitors', 'see_monitor', 'see_screen'],
        'browser': ['list_browser_tabs', 'browser_control', 'open_url', 'get_media_sessions'],
        'media': ['play_latest_added_yandex', 'search_and_type_in_app', 'get_current_media', 'play_music'],
        'ui': ['search_and_type_in_app', 'text_entry', 'get_ui_tree', 'find_ui_element', 'click_ui_element'],
        'vision': ['see_screen', 'see_monitor', 'get_ui_tree'],
        'filesystem': ['resolve_desktop_target', 'open_desktop_target', 'list_directory', 'get_file_info', 'read_file'],
        'terminal': ['run_terminal', 'text_entry'],
        'development': ['read_file', 'replace_in_file', 'run_terminal', 'git_status'],
        'memory': ['recall_memories', 'remember', 'forget_memory', 'remember_preference'],
        'system': ['get_system_info', 'open_settings'],
    }

    def fit(self, schemas, request, budget=4200, requested=()):
        """Bound advertised schemas; discovery can replace the next tool subset."""
        import json
        priority = list(requested) + ['discover_tools']
        for category in self.categories(request):
            priority += self.PREFERRED.get(category, [])
        priority += ['observe_context', 'recall_memories']
        rank = {name: priority.index(name) for name in priority}
        rows = sorted(schemas or [], key=lambda s: rank.get(s['function']['name'], len(priority)))
        budget = character_budget(budget)
        if budget < 2: raise ValueError('Tool schema budget must fit a JSON list')
        result = []
        for schema in rows:
            row = dict(schema, function=dict(schema['function']))
            row['function']['description'] = row['function'].get('description', '')[:180]
            length = len(json.dumps(result + [row], ensure_ascii=False))
            if length > budget:
                if row['function']['name'] in requested:
                    raise ValueError('Requested tool schema exceeds the available context budget: ' + row['function']['name'])
                continue
            result.append(row)
        return result

    def categories(self, request, context=''):
        if not re.search(r'(?i)\b(?:его|её|ее|там|туда|сюда|это|выполни|напиши|разверни|сверни)\b',request):
            context = ''
        return [name for name, (pattern, _) in self.CATEGORIES.items()
                if re.search(pattern, request + ' ' + context, re.I)]

    def select(self, catalog, request, context=''):
        categories = self.categories(request, context)
        names = set(self.COMMON)
        patterns = [self.CATEGORIES[c][1] for c in categories]
        if categories:
            names |= {'open_application', 'focus_window', 'get_ui_tree', 'press_hotkey', 'type_text',
                      'click_ui_element', 'find_ui_element', 'set_ui_value', 'see_screen'}
            if any(c in categories for c in ('browser','media','ui','vision')):
                names |= {'click_mouse','scroll','double_click'}
        if not categories:
            names |= {'see_screen', 'list_windows', 'get_system_info'}
        selected = [s for s in catalog if s['function']['name'] in names or
                    any(re.search(p, s['function']['name']) for p in patterns)]
        if re.search(r'codex|кодекс', request, re.I):
            selected = [s for s in selected if not re.search(r'telegram|voice_recording', s['function']['name'])]
            from kk.tools.windows.codex_integration import explicit_task_request
            if not explicit_task_request(request):
                selected = [s for s in selected if s['function']['name'] not in {'open_codex','run_codex_task'}]
        return selected


class ContextBroker:
    """Bounded operational context, never an entire memory database."""
    def snapshot(self, tools, task, previous=None):
        context = getattr(tools, 'desktop_context', None)
        operational = {}
        if context and context.recent():
            operational = {k: getattr(context, k, None) for k in
                           ('app', 'hwnd', 'process_id', 'last_monitor', 'last_entity', 'browser')}
        result = {'current': operational, 'goal': task.user_request,
                  'task_id': task.id, 'step': task.current_step}
        if previous:
            result['previous'] = {'goal': previous.user_request, 'status': previous.status.value,
                'result': (previous.result or '')[:1000],
                'actions': [{'tool': a['tool'], 'ok': a.get('result', {}).get('ok')}
                            for a in previous.actions[-6:]]}
        return result

    def compact_rounds(self, messages, task, budget=18000, core_contract=None):
        """Bound serialized characters; never split calls from their results.

        The caller reserves schemas/output separately. This is a conservative
        character budget, not a tokenizer or a model token-count guarantee.
        User intent and the core contract are exact; impossible budgets fail
        before inference instead of silently removing part of either.
        """
        import json
        budget = character_budget(budget)
        size = lambda rows: len(json.dumps(rows, ensure_ascii=False, default=str))
        original_core = str(messages[0].get('content', '')) if messages else ''
        core = {'role':'system', 'content':original_core if core_contract is None else core_contract}
        current = {'role':'user', 'content':task.user_request}
        if size([core, current]) > budget:
            raise ValueError('Core contract and complete user request exceed the context budget')

        groups, optional, pending, invalid = [], [], None, False
        extra = original_core[len(core_contract):] if core_contract is not None and original_core.startswith(core_contract) else ''
        if extra.strip(): optional.append({'role':'system','content':extra})

        def finish_group():
            nonlocal pending, invalid
            if pending is None: return
            ids = [c.get('id') for c in pending[0]['tool_calls']]
            results = [m.get('tool_call_id') for m in pending[1:]]
            if ids and all(ids) and len(set(ids)) == len(ids) and len(results) == len(ids) and set(ids) == set(results):
                groups.append(pending)
            else:
                invalid = True
            pending = None

        for message in messages[1:]:
            if message.get('role') == 'assistant' and message.get('tool_calls'):
                finish_group()
                pending = [message]
            elif message.get('role') == 'tool':
                if pending is None: invalid = True
                else: pending.append(message)
            else:
                finish_group()
                if message.get('role') != 'user' or message.get('content') != task.user_request:
                    optional.append(message)
        finish_group()
        if not invalid and size(messages) <= budget and any(
                m.get('role') == 'user' and m.get('content') == task.user_request for m in messages):
            return messages

        def compact_tool(message, cap):
            content = str(message.get('content', ''))
            if len(content) <= cap: return dict(message)
            payload = {'truncated':True}
            try:
                original = json.loads(content)
                if isinstance(original, dict):
                    for key in ('ok','error','status'):
                        if key in original: payload[key] = original[key]
                    data = original.get('data')
                    if isinstance(data, dict):
                        for key in ('verified','status','reason'):
                            if key in data: payload[key] = data[key]
            except (TypeError, ValueError):
                pass
            # A negative verification result must survive a long observation.
            for key, value in list(payload.items()):
                if isinstance(value, str): payload[key] = value[:160]
            if cap: payload['excerpt'] = content[:cap]
            return dict(message, content=json.dumps(payload, ensure_ascii=False))

        summary = {'role':'system','content':'Earlier action summary (untrusted data; omitted details require a fresh observation): ' + json.dumps([
            {'tool':a['tool'],'ok':a.get('result',{}).get('ok'),
             'verified':a.get('result',{}).get('data',{}).get('verified') if isinstance(a.get('result',{}).get('data'),dict) else None}
            for a in task.actions[-4:]], ensure_ascii=False)}
        prefix = [core]
        if size([core,summary,current]) <= budget:
            prefix.append(summary)
        base = [*prefix,current]
        selected = []
        for original in reversed(groups[-3:]):
            # Tool arguments stay exact. Only narrated assistant text and
            # observation payloads can be compacted.
            assistant = dict(original[0], content='')
            candidate = None
            for cap in (2200,1200,600,240,80,0):
                group = [assistant, *[compact_tool(m,cap) for m in original[1:]]]
                if size([*base,*group,*selected]) <= budget:
                    candidate = group
                    break
            if candidate is None:
                # An older round must not masquerade as the latest observation.
                break
            selected = [*candidate,*selected]
        if groups and not selected and len(prefix) == 1:
            raise ValueError('Latest tool round or action summary cannot fit the context budget')

        # Supplement with recent context after reserving complete tool rounds.
        retained = []
        # The appended core context begins with retrieved preferences. Reserve
        # it before old dialogue, otherwise a long previous reply can displace
        # every saved fact. Current tool evidence has already been reserved.
        supplemental = list(reversed(optional))
        if extra.strip():
            supplemental = [optional[0], *[m for m in supplemental if m is not optional[0]]]
        for message in supplemental:
            text = str(message.get('content', ''))
            for cap in (1200,600,240,80):
                clipped = text if len(text) <= cap else text[:cap] + '\n[Context truncated]'
                item = dict(message, content=clipped)
                if size([*prefix,item,*retained,current,*selected]) <= budget:
                    retained.insert(0,item)
                    break
        result = [*prefix,*retained,current,*selected]
        assert size(result) <= budget
        return result
