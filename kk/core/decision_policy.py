"""Shared defaults for deterministic commands and the local planner."""
from dataclasses import dataclass
from time import monotonic
import json
from pathlib import Path


@dataclass
class OperationalContext:
    app: str = ''
    hwnd: int = 0
    updated: float = 0
    process_id: int = 0
    last_monitor: int | None = None
    last_window: dict | None = None
    last_entity: str = ''
    last_action: str = ''
    browser: str = ''

    def recent(self):
        return self.app if monotonic() - self.updated < 300 else ''

    @property
    def last_app(self): return self.app

    @property
    def last_hwnd(self): return self.hwnd


class DecisionPolicy:
    def __init__(self, preferences=None, high_confidence=.84, context_confidence=.65, path=None, memory=None):
        self.preferences = dict(preferences or {})
        self.defaults = dict(self.preferences)
        self.memory = memory
        self.path = Path(path) if path else None
        self.saved = {}
        if self.path and self.path.exists():
            saved=json.loads(self.path.read_text(encoding='utf-8'))
            self.saved = {k:v for k,v in saved.items() if k in {'telegram_client','browser','music_app'} and isinstance(v,str)}
            self.preferences.update(self.saved)
        self.high_confidence = high_confidence
        self.context_confidence = context_confidence
        if memory:
            # Import only the prior explicitly saved file. Configuration is a
            # default, not a user fact. SQLite becomes the authoritative store.
            legacy = self.saved
            for name in {'telegram_client','browser','music_app'}:
                stored = memory.get('application.' + name)
                if stored:
                    self.preferences[name] = stored['value']
                    self.saved[name] = stored['value']
                elif isinstance(legacy.get(name), str):
                    memory.remember('application.' + name, legacy[name], source='legacy_preference')

    def remember(self, preference, application):
        if preference not in {'telegram_client','browser','music_app'} or not application.strip():
            raise ValueError('Unsupported application preference')
        self.preferences[preference]=application
        self.saved[preference]=application
        if self.memory:
            self.memory.remember('application.' + preference, application, source='user')
        if self.path:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.saved,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(self.path)
        return dict(preference=preference,application=application,verified=True)

    def forget(self, preference):
        if preference not in {'telegram_client','browser','music_app'}:
            return False
        existed = bool(self.memory and self.memory.forget('application.' + preference))
        existed = self.saved.pop(preference, None) is not None or existed
        if self.path and self.path.exists():
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.saved, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.path)
        self.preferences.pop(preference, None)
        if preference in self.defaults:
            self.preferences[preference] = self.defaults[preference]
        return existed

    def clarification_budget(self, confidence, strong_context=False):
        return 0 if confidence >= self.high_confidence or (strong_context and confidence >= self.context_confidence) else 1

    def application(self, name):
        from kk.tools.windows.app_resolver import canonical
        preference = {'telegram': 'telegram_client', 'browser': 'browser'}.get(canonical(name))
        if name.casefold() in {'музыка','плеер','music','music player'}:
            preference = 'music_app'
        # Explicit client names remain authoritative.
        if name.casefold() in {'ayugram', 'telegram desktop'}: return name
        return self.preferences.get(preference) or name

    def monitor(self, monitors, context=None, foreground=None, explicit=None):
        valid = {m['index'] for m in monitors}
        if explicit is not None:
            if explicit not in valid: raise LookupError(f'Монитор {explicit} не подключён')
            return explicit
        if context and context.recent() and context.last_monitor in valid and (
                not foreground or foreground.get('hwnd') == context.hwnd):
            return context.last_monitor
        if foreground and foreground.get('monitor_index') in valid:
            return foreground['monitor_index']
        if context and context.recent() and context.last_monitor in valid:
            return context.last_monitor
        return next((m['index'] for m in monitors if m.get('primary')), next(iter(valid), None))

    def prompt(self):
        return ('\nТы — Onyx (Оникс). Выполняй ясное намерение без уточнений. '
                'Не спрашивай монитор, HWND, путь или механизм запуска: используй контекст, '
                'предпочтения и primary monitor. LOOK BEFORE ASKING: сначала index, окна, '
                'UIA, OCR, Vision и память. Для ясной задачи clarification_budget=0; '
                'при существенной неоднозначности разрешён один конкретный вопрос. '
                'Не подтверждай успех до наблюдаемой проверки. Начать запись голосового '
                'не означает отправить: отправка только по отдельному явному запросу. '
                f'Предпочтения: {self.preferences}.')
