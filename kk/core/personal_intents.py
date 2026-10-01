"""Explicit personal phrases; stored data never becomes executable Python/shell."""
import json
import re
from pathlib import Path
from threading import RLock


def key(text):
    return re.sub(r'\s+', ' ', text.casefold().strip(' .!?"«»'))


class PersonalIntentRegistry:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = RLock()
        self.entries = {}
        defaults = json.loads(Path(__file__).with_name('app_aliases.json').read_text(encoding='utf-8'))
        for kind, targets in defaults.items():
            for target, phrases in targets.items():
                for phrase in phrases:
                    self.entries[key(phrase)] = {'kind': kind, 'target': target}
        if self.path and self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            for phrase, entry in saved.items():
                self.validate(entry)
                self.entries[key(phrase)] = entry

    @staticmethod
    def validate(entry):
        if entry.get('kind') not in {'app', 'url', 'skill', 'task'} or not entry.get('target'):
            raise ValueError('Alias needs app, url, skill or task target')
        if entry['kind'] == 'url':
            from urllib.parse import urlsplit
            url = urlsplit(entry['target'])
            if url.scheme not in {'http', 'https'} or not url.netloc:
                raise ValueError('Alias URL must be http/https')
        if entry['kind'] == 'task' and not isinstance(entry['target'], list):
            raise ValueError('Task alias needs structured steps')
        if entry['kind'] == 'task':
            required = {'open': {'app'}, 'move': {'app','monitor'}, 'volume': {'level','valid'},
                        'url': {'url'}, 'game': {'game'}, 'skill': {'name'}}
            for step in entry['target']:
                if not isinstance(step,dict) or step.get('kind') not in required or not required[step['kind']] <= step.keys():
                    raise ValueError('Unsupported or incomplete task alias step')
        elif not isinstance(entry['target'],str):
            raise ValueError('Alias target must be text')

    def remember(self, phrase, kind, target):
        entry = dict(kind=kind, target=target)
        self.validate(entry)
        with self.lock:
            self.entries[key(phrase)] = entry
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix('.tmp')
                temp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding='utf-8')
                temp.replace(self.path)
        return {'alias': phrase, 'application': target}

    def resolve(self, phrase):
        return self.entries.get(key(phrase))

    def target(self, text):
        text = text.strip(' .!?"«»')
        if text.casefold().startswith('навык '):
            return dict(kind='skill', target=text[6:].strip())
        if re.search(r',|\s(?:и|потом|and)\s', text, re.I):
            from .desktop_commands import parse_plan
            plan = parse_plan('открой '+text, intents=self)
            if plan and len(plan)>1: return dict(kind='task', target=plan)
        text = re.sub(r'^(?:сайт|приложение)\s+', '', text, flags=re.I)
        known = self.resolve(text)
        if known: return dict(known)
        if text.casefold() in {'chatgpt', 'чат gpt'}:
            return dict(kind='url', target='https://chatgpt.com')
        return dict(kind='url' if text.startswith(('https://', 'http://')) else 'app', target=text)

    def learning(self, text):
        if text.casefold().startswith('запомни'): return None
        if re.match(r'(?i)^(?:что|кто|какая|какой|какое|какие|почему|зачем|где|как)\b', text): return None
        patterns = [r'Когда я говорю\s+["«]?(.+?)["»]?,?\s+открывай\s+(.+)',
                    r'(?:Нет,?\s+)?под\s+["«]?(.+?)["»]?\s+я имею в виду\s+(.+)',
                    r'(.+?)\s+(?:—\s*(?:это\s+)?|это\s+|=\s*)(.+)']
        for index, pattern in enumerate(patterns):
            if index == 2:
                # Declarative X = Y aliases must not consume action clauses
                # such as "закрой это окно" or "открой файл X = Y".
                # Explicit learning forms above can still name literal verbs.
                unquoted = re.sub(r'"[^"\n]*"|«[^»\n]*»', '', text)
                if re.search(r'(?i)\b(?:открой|открыть|закрой|разверни|сверни|восстанови|запусти|'
                             r'напиши|введи|вставь|выполни|покажи|найди|прочитай|скажи|'
                             r'создай|сохрани|удали|переименуй|перемести|перенеси|сделай|'
                             r'open|close|launch|type|write|run|delete|move|rename)\b', unquoted):
                    continue
            match = re.fullmatch(pattern, text.strip(' .!?'), re.I)
            if match:
                return dict(kind='personal_alias', phrase=match[1].strip('"«»'), **{'entry': self.target(match[2])})
        return None


DEFAULT_INTENTS = PersonalIntentRegistry()


def corrected(text):
    """Corrections operate on command syntax; quoted names remain byte-exact."""
    from uuid import uuid4
    quoted = {}
    def protect(match):
        key = '__quoted_' + uuid4().hex + '__'
        quoted[key] = match.group()
        return key
    protected = re.sub(r'"[^"\n]*"|«[^»\n]*»', protect, text)
    result = _corrected_unquoted(protected)
    for key, value in quoted.items(): result = result.replace(key, value)
    return result


def _corrected_unquoted(text):
    """Resolve only explicit local replacements; ambiguous language stays with Brain."""
    text = re.sub(r'\.{2,}|…|[.!?](?=\s)', ', ', text).strip(' ,.!?')
    reorder = re.fullmatch(r'(открой .+?),?\s*хотя\s+сначала\s+(.+)', text, re.I)
    if reorder:
        return 'открой ' + re.sub(r',?\s*потом\s+', ', ', reorder[2])
    match = re.fullmatch(r'(.+?)\s*,?\s*нет,?\s+(.+)', text, re.I)
    if match:
        before, after = match[1].strip(' ,'), match[2].strip(' ,')
        after = re.sub(r'(?i)^лучше\s+', '', after)
        if re.search(r'\sна\s+(первый|первом|второй|втором|\d+)(?:\s+экране?)?$', before, re.I) and after.casefold().startswith('на '):
            stem = re.sub(r'\sна\s+.*$', '', before, flags=re.I)
            return (stem if stem.casefold().startswith(('перенеси ', 'открой ')) else 'перенеси '+stem) + ' ' + after
        if re.match(r'^(?:открой|запусти)\s+', before, re.I) and not re.search(r',|\sи\s', before):
            return after if re.match(r'^(открой|запусти)\s', after, re.I) else 'открой ' + after
        if re.match(r'(?i)^(?:(?:поставь|сделай|установи)\s+)?громкость\s+', before):
            return after if after.casefold().startswith('громкость') else 'громкость ' + after
    return re.sub(r',?\s+потом\s+', ', ', text, flags=re.I)
