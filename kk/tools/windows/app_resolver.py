"""One typed desktop resolver, with bounded installed-app and Shell discovery."""
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
from time import monotonic


GROUPS = {
    'powershell': ['powershell', 'windows powershell', 'пауэршелл', 'повер шел', 'повершелл', 'пауэр шелл'],
    'pwsh': ['pwsh', 'powershell 7'],
    'explorer': ['explorer', 'проводник', 'файловка', 'file explorer'],
    'codex': ['codex', 'кодекс', 'openai codex'],
    'telegram': ['telegram', 'телеграм', 'телеграмм', 'телега', 'ayugram', 'аюграм', 'tg', 'тг'],
    'yandexmusic': ['yandex music', 'яндекс музыка', 'яндекс музыку', 'яндекс музыке', 'яндексмузыка'],
    'vscode': ['vs code', 'visual studio code', 'vscode', 'code', 'вс код', 'ви эс код', 'вэ эс код', 'vs код', 'vs-код'],
    'visualstudio': ['visual studio', 'visualstudio', 'devenv', 'вижуал студио', 'визуал студио'],
    'browser': ['browser', 'браузер'],
    'discord': ['discord', 'дискорд'],
    'notepad': ['notepad', 'блокнот', 'блокноте'],
    'obsidian': ['obsidian', 'обсидиан', 'обсидиана', 'обсидиане'],
}
TRANSLIT = dict(zip('абвгдеёзийклмнопрстуфхыэ', ['a','b','v','g','d','e','e','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','y','e']))
TRANSLIT.update(ж='zh', ц='ts', ч='ch', ш='sh', щ='shch', ю='yu', я='ya', ь='', ъ='')


def normalized(name):
    return re.sub(r'[^a-zа-яё0-9]', '', name.casefold().removesuffix('.exe'))


def transliterated(name):
    return ''.join(TRANSLIT.get(c, c) for c in normalized(name)).replace('ks', 'x')


def canonical(name):
    key = normalized(name)
    # Installed shortcuts include an edition/year; never fuzzy-match these
    # against Visual Studio Code, which is a separate executable and product.
    if re.fullmatch(r'visualstudio(?:community|professional|enterprise)?(?:20\d{2})?', key):
        return 'visualstudio'
    return next((group for group, aliases in GROUPS.items() if key in [normalized(a) for a in aliases]), key)


@dataclass(frozen=True)
class AppEntry:
    name: str
    kind: str
    target: str
    executable: str = ''


TARGET_TYPES = {'application', 'window', 'folder', 'file', 'image', 'document',
                'browser_tab', 'webpage', 'virtual_shell_item', 'agent_ui'}
DOCUMENT_EXTENSIONS = {'.txt', '.md', '.markdown', '.pdf', '.rtf', '.doc', '.docx', '.odt',
                       '.xls', '.xlsx', '.ods', '.ppt', '.pptx', '.odp', '.csv', '.tsv',
                       '.json', '.xml', '.yaml', '.yml', '.ini', '.log', '.tex', '.html', '.htm'}


class TargetClarificationRequired(LookupError):
    def __init__(self, question):
        self.question = question
        super().__init__(question)


@dataclass(frozen=True)
class DesktopTarget:
    identity: str
    type: str
    name: str
    source: str
    verified_at: str
    scope: str
    path: str = ''
    hwnd: int = 0
    executable: str = ''
    process_id: int = 0


def _target_name(name):
    # File punctuation/extensions remain part of identity; application aliases
    # are deliberately not used for folders or documents.
    return str(name).casefold().strip(' \t\r\n"«»')


def _filesystem_spelling(name):
    # Unlike application normalization, extensions and punctuation are retained.
    folded = _target_name(name)
    return ''.join(TRANSLIT.get(c, c) for c in folded).replace('x', 'ks').replace('y', 'i')


def _adjacent_typo(left, right):
    left, right = _filesystem_spelling(left), _filesystem_spelling(right)
    return len(left) >= 4 and len(left) == len(right) and any(
        left[:i] + left[i + 1] + left[i] + left[i + 2:] == right for i in range(len(left) - 1))


class ApplicationResolver:
    def __init__(self, aliases=None, corrections_path=None, powershell='auto'):
        self.aliases = aliases or {}
        self.corrections_path = Path(corrections_path) if corrections_path else None
        self.corrections = {}
        if self.corrections_path and self.corrections_path.exists():
            try: self.corrections = json.loads(self.corrections_path.read_text(encoding='utf-8'))
            except (ValueError, OSError): pass
        self.powershell = powershell
        self.entries = []
        self.updated = 0
        self.discovery_errors = []
        self.metadata_cache = {}
        self.favorite_folders = []
        self.cache_path = self.corrections_path.with_name('app_index.json') if self.corrections_path else None
        if self.cache_path and self.cache_path.exists():
            try:
                cached = json.loads(self.cache_path.read_text(encoding='utf-8'))
                self.entries = [AppEntry(**e) for e in cached if e.get('kind') != 'window' and
                    (e.get('kind') == 'aumid' or Path(e.get('target','')).exists())]
                if self.entries: self.updated = monotonic()-100
            except (ValueError, TypeError, OSError): pass

    def remember(self, spoken, application):
        entries = self.resolve(application)
        self.corrections[normalized(spoken)] = entries[0].name
        if self.corrections_path:
            self.corrections_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.corrections_path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.corrections, ensure_ascii=False), encoding='utf-8')
            temp.replace(self.corrections_path)
        return {'alias': spoken, 'application': entries[0].name}

    def refresh(self):
        entries = []
        self.discovery_errors = []
        def add(name, kind, target, executable=''):
            if target:
                entries.append(AppEntry(name, kind, target, executable))
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('app_index.ps1'))],
                capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            for row in json.loads(result.stdout.lstrip('\ufeff') or '[]'):
                add(**row)
        except Exception as exc:
            self.discovery_errors.append(str(exc))
        for name, target in self.aliases.items():
            resolved = shutil.which(target) or (str(Path(target)) if Path(target).is_file() else '')
            add(name, 'executable', resolved, resolved)
        # Enumerate only the immediate PATH directories, never arbitrary drives.
        for folder in dict.fromkeys(os.environ.get('PATH', '').split(os.pathsep)):
            try:
                for path in Path(folder).glob('*.exe'):
                    add(path.stem, 'path', str(path), str(path))
            except OSError: pass
        known = {
            'powershell': os.path.join(os.environ.get('SystemRoot', 'C:/Windows'), 'System32/WindowsPowerShell/v1.0/powershell.exe'),
            'explorer': os.path.join(os.environ.get('SystemRoot', 'C:/Windows'), 'explorer.exe'),
            'pwsh': os.path.join(os.environ.get('ProgramFiles', 'C:/Program Files'), 'PowerShell/7/pwsh.exe'),
            'vscode': os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs/Microsoft VS Code/Code.exe'),
            'yandexmusic': os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs/YandexMusic/Яндекс Музыка.exe'),
        }
        for name, target in known.items():
            if Path(target).is_file(): add(name, 'executable', target, target)
        import psutil
        for process in psutil.process_iter(['name', 'exe']):
            try:
                path = process.info['exe']
                if path: add(Path(path).stem, 'executable', path, path)
            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        from .windows import list_visible_windows
        from kk.ui.capture_guard import is_own_window
        for window in list_visible_windows():
            if window.get('executable') and not is_own_window(window.get('hwnd')):
                add(Path(window['executable']).stem, 'window', str(window['hwnd']), window['executable'])
        # CODEX by Waves is a synthesizer, not the OpenAI desktop client.
        def label(e):
            path = (e.target + e.executable).casefold()
            if 'openai.codex_' in path and (e.target.endswith('!App') or Path(e.executable).name.casefold() in {'chatgpt.exe', 'codex.exe'}):
                return AppEntry('OpenAI Codex', e.kind, e.target, e.executable)
            if canonical(e.name) == 'codex':
                return AppEntry('Waves CODEX' if 'waves' in path else 'Codex CLI', e.kind, e.target, e.executable)
            return e
        self.entries = list(dict.fromkeys(label(e) for e in entries))
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True,exist_ok=True)
            self.cache_path.write_text(json.dumps([asdict(e) for e in self.entries if e.kind != 'window'],ensure_ascii=False),encoding='utf-8')
        self.discover_telegram_preference()
        self.updated = monotonic()
        return {'entries': len(self.entries), 'errors': self.discovery_errors}

    def discover_telegram_preference(self):
        decision = getattr(self,'decision_policy',None)
        if not decision: return
        clients = [e for e in self.entries if e.executable and
                   (canonical(e.name) == 'telegram' or (Path(e.executable).parent/'tdata').is_dir())]
        if not clients: return
        preferred = decision.preferences.get('telegram_client','')
        if any(normalized(e.name) == normalized(preferred) for e in clients): return
        selected = sorted(clients,key=lambda e:(e.kind!='window', e.name.casefold()))[0]
        decision.remember('telegram_client', selected.name)

    def resolve(self, name, refresh=False):
        if refresh or not self.updated or monotonic() - self.updated > 120:
            self.refresh()
        requested = self.corrections.get(normalized(name), name)
        decision = getattr(self, 'decision_policy', None)
        if decision and normalized(name) not in self.corrections:
            requested = decision.application(requested)
        key = canonical(requested)
        if key == 'powershell':
            preferred = self.powershell
            if preferred == 'auto': preferred = 'pwsh' if any(canonical(e.name) == 'pwsh' and '.cache' not in e.target.casefold() and 'codex' not in e.target.casefold() for e in self.entries) else 'powershell'
            key = canonical(preferred)
        matches = [e for e in self.entries if canonical(e.name) == key]
        # Explicit client selection must not collapse back into the Telegram preference.
        if normalized(requested) in {'telegramdesktop', 'ayugram'}:
            wanted = 'ayugram' if normalized(requested) == 'ayugram' else 'telegram'
            matches = [e for e in self.entries if normalized(Path(e.executable or e.target).name) == wanted]
            if not matches: raise LookupError(f'Клиент «{requested}» не установлен')
        configured = self.aliases.get(requested.casefold())
        if configured:
            matches = [e for e in self.entries if e.executable and os.path.normcase(e.executable) == os.path.normcase(configured)] + matches
        if not matches:
            names = {}
            for e in self.entries:
                if key in {'vscode', 'visualstudio'} and canonical(e.name) != key:
                    continue
                similarity = SequenceMatcher(None, transliterated(requested), transliterated(e.name)).ratio()
                group = canonical(e.name)
                names[group] = max(names.get(group, 0), similarity)
            ranked = sorted(names.items(), key=lambda x: x[1], reverse=True)
            threshold = decision.high_confidence if decision else .84
            if ranked and ranked[0][1] >= threshold and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= .10):
                matches = [e for e in self.entries if canonical(e.name) == ranked[0][0]]
        if not matches:
            if not refresh:
                return self.resolve(name, refresh=True)
            raise LookupError(f'Приложение «{name}» не найдено после обновления индекса.')
        order = {'window': 0, 'executable': 1, 'aumid': 2, 'shortcut': 3, 'path': 4}
        # Prefer the configured executable, and native 64-bit PowerShell.
        return sorted(dict.fromkeys(matches), key=lambda e: (0 if configured and e.executable == configured else 1,
            order[e.kind], 'syswow64' in e.target.casefold(), e.name.casefold()))

    def inspect(self, name):
        try: return {'found': True, 'candidates': [asdict(e) for e in self.resolve(name)]}
        except LookupError as exc: return {'found': False, 'error': str(exc)}

    def invalidate_metadata(self, path=''):
        """Mutation notifications invalidate bounded metadata, never file contents."""
        self.metadata_cache.clear()

    def metadata(self, location='', refresh=False):
        from kk.tools.observation import observation
        from .shell_metadata import known_folder, directory_snapshot, shell_snapshot, path_identity
        location_key = _target_name(location)
        desktop = {'desktop', 'рабочий стол', 'рабочем столе', 'на рабочем столе'}
        downloads = {'downloads', 'загрузки', 'загрузках', 'в загрузках'}
        scope = 'desktop' if location_key in desktop else 'downloads' if location_key in downloads else location
        key = str(scope or 'bounded_favorites')
        cached = self.metadata_cache.get(key)
        if cached and not refresh and monotonic() - cached[0] < 20:
            return cached[1]
        items, limitations, scopes = [], [], []
        complete = True
        if not scope or scope == 'desktop':
            scopes.append('desktop')
            try:
                snapshot = shell_snapshot('desktop')
                items.extend(snapshot['items'])
                limitations.extend(snapshot['limitations'])
                complete &= snapshot['completeness'] == 'complete'
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                complete = False
                limitations.append('Shell Desktop unavailable: ' + str(exc))
            # Known Folder roots include user/public and redirected OneDrive.
            for root_name in ('desktop', 'public_desktop'):
                try:
                    root = known_folder(root_name)
                    snapshot = directory_snapshot(root, 'KnownFolder:' + root_name)
                    items.extend(snapshot['items'])
                    limitations.extend(snapshot['limitations'])
                    complete &= snapshot['completeness'] == 'complete'
                except OSError as exc:
                    complete = False
                    limitations.append(root_name + ': ' + str(exc))
        roots = []
        if not scope or scope == 'downloads':
            try:
                roots.append((known_folder('downloads'), 'KnownFolder:downloads'))
            except OSError as exc:
                complete = False
                limitations.append('downloads: ' + str(exc))
        if not scope:
            roots.extend((Path(p), 'favorite') for p in self.favorite_folders[:10])
        elif scope not in {'desktop', 'downloads'}:
            absolute = Path(scope).expanduser()
            if not absolute.is_absolute():
                raise ValueError('Укажи рабочий стол, загрузки или абсолютный путь папки')
            roots.append((absolute, 'explicit_location'))
        for root, source in roots:
            scopes.append(str(root))
            try:
                snapshot = directory_snapshot(root, source)
                items.extend(snapshot['items'])
                limitations.extend(snapshot['limitations'])
                complete &= snapshot['completeness'] == 'complete'
            except OSError as exc:
                complete = False
                limitations.append(str(exc))
        unique = {}
        for item in items:
            identity = path_identity(item['path']) if not item['path'].startswith('::') else item['path'].casefold()
            # Prefer Shell's resolved .lnk target over the filesystem filename.
            if identity not in unique:
                unique[identity] = item
        metadata_source = ('filesystem' if scope and scope not in {'desktop', 'downloads'} else
                           'KnownFolder:downloads + filesystem' if scope == 'downloads' else
                           'Known Folders + Windows Shell')
        result = observation(list(unique.values()), source=metadata_source,
            scope=scopes, completeness='complete' if complete else 'partial', limitations=limitations)
        self.metadata_cache[key] = (monotonic(), result)
        # At most 16 requested locations retained; each is a shallow snapshot.
        while len(self.metadata_cache) > 16:
            self.metadata_cache.pop(next(iter(self.metadata_cache)))
        return result

    def resolve_windows(self, name):
        """Resolve an existing app by executable identity, never title similarity."""
        from .windows import list_visible_windows
        requested = self.corrections.get(normalized(name), name)
        key = canonical(requested)
        exact_executables = {
            'vscode': {'code.exe'}, 'visualstudio': {'devenv.exe'},
            'obsidian': {'obsidian.exe'}, 'notepad': {'notepad.exe'},
            'explorer': {'explorer.exe'}, 'powershell': {'powershell.exe'},
            'pwsh': {'pwsh.exe'},
        }
        windows = list_visible_windows()
        if key in exact_executables:
            return [w for w in windows if Path(w.get('executable', '')).name.casefold() in exact_executables[key]]
        # An observed process name is evidence; a document title bearing the
        # same words is not. Resolve aliases to installed executable paths too.
        matches = [w for w in windows if w.get('executable') and
                   canonical(Path(w['executable']).stem) == key]
        if matches:
            return matches
        try:
            paths = {os.path.normcase(e.executable) for e in self.resolve(requested) if e.executable}
        except LookupError:
            paths = set()
        return [w for w in windows if os.path.normcase(w.get('executable', '')) in paths]

    def resolve_target(self, name, type='application', location='', refresh=False):
        """Return typed candidates plus honest observation coverage.

        Explicit type/location never falls through to application fuzzy matching.
        Cached metadata helps discovery; candidates are revalidated before use.
        """
        from kk.tools.observation import observation
        from .shell_metadata import timestamp, path_identity
        if type not in TARGET_TYPES:
            raise ValueError('Unknown desktop target type: ' + type)
        checked, targets, near_targets, limitations = timestamp(), [], [], []
        scope, source, completeness = location or 'desktop_session', 'desktop_resolver', 'complete'
        if type == 'application':
            source = 'installed_application_index'
            try:
                for entry in self.resolve(name, refresh=refresh):
                    targets.append(DesktopTarget('application:' + (entry.executable or entry.target).casefold(),
                        type, entry.name, entry.kind, checked, scope,
                        path=entry.target if entry.kind != 'window' else '',
                        hwnd=int(entry.target) if entry.kind == 'window' else 0, executable=entry.executable))
            except LookupError as exc:
                limitations.append(str(exc))
            limitations.extend(self.discovery_errors)
            completeness = 'partial' if limitations else 'complete'
        elif type == 'window':
            source = 'EnumWindows + process executable'
            for row in self.resolve_windows(name):
                targets.append(DesktopTarget(f"window:{row['hwnd']}:{row.get('process_id', 0)}", type,
                    row['title'], source, checked, scope, hwnd=row['hwnd'],
                    executable=row.get('executable', ''), process_id=row.get('process_id', 0)))
        elif type in {'folder', 'file', 'image', 'document', 'virtual_shell_item'}:
            asked = _target_name(name)
            extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tiff'}
            absolute = Path(name)
            if absolute.is_absolute():
                # Presence AND absence are observations of this path alone.
                # Unrelated Desktop/Downloads discovery cannot establish its
                # coverage, and must not substitute another identically named item.
                source, scope, completeness, rows = 'explicit_path', str(absolute), 'complete', []
                try:
                    import stat
                    metadata = absolute.stat()
                    rows = [dict(name=absolute.name, path=str(absolute),
                        type='folder' if stat.S_ISDIR(metadata.st_mode) else 'file', source=source)]
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    completeness = 'unknown'
                    limitations.append('Explicit path metadata unavailable: ' + str(exc))
            else:
                snapshot = self.metadata(location, refresh)
                source, scope, completeness = snapshot['source'], snapshot['scope'], snapshot['completeness']
                limitations.extend(snapshot['limitations'])
                rows = snapshot['items']
            for row in rows:
                path = row.get('target_path') or row['path']
                virtual = row['type'] == 'virtual_shell_item'
                recycle = type == 'virtual_shell_item' and asked in {'корзина', 'корзину', 'мусорка', 'мусорку', 'recycle bin'} and '645ff040-5081-101b-9f08-00aa002f954e' in path.casefold()
                aliases = {_target_name(row['name']), _target_name(Path(row['path']).name), _target_name(row['path'])}
                # Bilingual names such as Onyx/Оникс are exact transliteration
                # candidates within this already explicit type and location.
                # This never invokes application aliases or fuzzy similarity.
                transliteration_match = (not absolute.is_absolute() and
                    _filesystem_spelling(name) == _filesystem_spelling(row['name']))
                exact_match = recycle or asked in aliases or transliteration_match
                near_match = not absolute.is_absolute() and _adjacent_typo(name, row['name'])
                if not exact_match and not near_match:
                    continue
                kind = row['type']
                if type == 'folder' and kind != 'folder': continue
                if type in {'file', 'image', 'document'} and kind != 'file': continue
                if type == 'image' and Path(path).suffix.casefold() not in extensions: continue
                if type == 'document' and Path(path).suffix.casefold() not in DOCUMENT_EXTENSIONS: continue
                if type == 'virtual_shell_item' and not virtual: continue
                if not virtual and (not Path(path).exists() or (type == 'folder') != Path(path).is_dir()):
                    self.invalidate_metadata(path)
                    limitations.append('Stale entry invalidated: ' + row['name'])
                    continue
                identity = path.casefold() if virtual else path_identity(path)
                target = DesktopTarget(type + ':' + identity, type, row['name'], row['source'], checked,
                    str(scope), path=path)
                (targets if exact_match else near_targets).append(target)
        elif type == 'webpage':
            from urllib.parse import urlsplit
            parsed = urlsplit(name)
            if parsed.scheme not in {'https', 'http'} or not parsed.hostname:
                raise ValueError('Для webpage нужен явный HTTP(S) адрес')
            targets.append(DesktopTarget('webpage:' + name, type, name, 'explicit_url', checked, scope, path=name))
            completeness = 'unknown'
            limitations.append('URL syntax validated; navigation and page identity not observed')
        elif type == 'agent_ui':
            completeness = 'unknown'
            limitations.append('Agent UI is distinct from user files; no application launch is inferred')
        else:
            completeness = 'unknown'
            limitations.append('Browser tab identity must come from the browser adapter tabId/windowId observation')
        # Multiple launch methods for one application are not separate targets.
        targets = list({t.identity: t for t in targets}.values())
        result = observation([asdict(t) for t in targets], source=source, scope=scope,
            completeness=completeness, limitations=limitations)
        result.update(found=bool(targets), ambiguous=len(targets) > 1 and type != 'application', type=type,
                      candidates=result['items'], near_candidates=[dict(asdict(t), match='adjacent_transposition',
                          needs_confirmation=True) for t in {t.identity: t for t in near_targets}.values()] if not targets else [])
        return result
