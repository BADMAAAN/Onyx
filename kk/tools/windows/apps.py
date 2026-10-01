from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any


def open_settings(page=''):
    import os
    if page not in {'', 'bluetooth', 'sound', 'display'}:
        raise ValueError('Unsupported settings page')
    uri = 'ms-settings:' + page
    os.startfile(uri)
    from time import monotonic, sleep
    from .windows import list_visible_windows, focus_window_handle
    from .native import foreground_window
    deadline = monotonic()+3
    focused = None
    while monotonic() < deadline:
        fg = foreground_window()
        if fg.get('executable','').casefold().endswith('systemsettings.exe') or (
            fg.get('executable','').casefold().endswith('applicationframehost.exe') and
            fg.get('title','').casefold() in {'параметры','настройки','settings'}):
            focused = fg
            break
        candidates=[w for w in list_visible_windows()
                    if (w.get('process','').casefold() in {'systemsettings.exe','applicationframehost.exe'} or
                        w['title'].casefold() in {'параметры','настройки','settings'})]
        candidates.sort(key=lambda w:(w.get('process','').casefold() != 'systemsettings.exe',
                                      w.get('minimized',False)))
        if candidates:
            try:
                focused = focus_window_handle(candidates[0]['hwnd'])
                break
            except OSError:
                pass
        sleep(.1)
    return {'requested_uri': uri, 'focused_window': focused,
            'verification_required': True}


class ApplicationLauncher:
    def __init__(self, aliases=None, resolver=None):
        from .app_resolver import ApplicationResolver
        self.resolver = resolver or ApplicationResolver(aliases)
        self.timeout = 5

    def _launch(self, entry):
        import os
        import psutil
        from .windows import list_visible_windows
        self._before_hwnds = {w['hwnd'] for w in list_visible_windows()}
        self._before_pids = set(psutil.pids())
        if entry.kind == 'aumid':
            os.startfile('shell:AppsFolder\\' + entry.target)
            return None
        if entry.kind == 'shortcut':
            if not Path(entry.target).is_file() or not entry.executable or not Path(entry.executable).is_file():
                raise FileNotFoundError('Application shortcut or executable disappeared')
            if Path(entry.executable).suffix.casefold() != '.exe':
                raise ValueError('A folder/document shortcut is not an application')
            os.startfile(entry.target)
            return None
        if entry.kind == 'window':
            return None
        if not Path(entry.target).is_file():
            raise FileNotFoundError(entry.target)
        flags = subprocess.CREATE_NEW_CONSOLE if Path(entry.target).name.casefold() in {'powershell.exe', 'pwsh.exe'} else 0
        stdio = {} if flags else dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return subprocess.Popen([entry.target], close_fds=True, creationflags=flags, **stdio).pid

    def _verify(self, entry, pid, cancellation_check):
        from .windows import list_visible_windows, focus_window_handle
        from .native import window_metadata
        from time import monotonic, sleep
        import os
        import psutil
        deadline = monotonic() + (self.timeout if entry.kind != 'window' else .3)
        while monotonic() < deadline:
            if cancellation_check: cancellation_check()
            candidates = []
            for w in list_visible_windows():
                if entry.kind == 'window':
                    match = (w['hwnd'] == int(entry.target) and bool(entry.executable) and
                             os.path.normcase(w.get('executable','')) == os.path.normcase(entry.executable))
                elif entry.executable:
                    match = os.path.normcase(w.get('executable','')) == os.path.normcase(entry.executable)
                else:
                    from .app_resolver import canonical
                    match = canonical(w.get('process','')) == canonical(entry.name)
                    if not match:
                        # Packaged apps expose their package id in their executable path.
                        package = entry.target.split('!')[0].split('_')[0]
                        match = '!' in entry.target and package.casefold() in w.get('executable','').casefold()
                if not match and pid:
                    try: match = w['process_id'] == pid or any(p.pid == pid for p in psutil.Process(w['process_id']).parents())
                    except (psutil.NoSuchProcess, psutil.AccessDenied): pass
                if not match and w.get('process','').casefold() in {'windowsterminal.exe', 'conhost.exe', 'openconsole.exe'}:
                    from .app_resolver import canonical
                    key = canonical(entry.name)
                    expected = 'pwsh.exe' if key == 'pwsh' else 'powershell.exe' if key == 'powershell' else ''
                    # Hosted console: require a newly launched shell process AND
                    # a new visible host window with the expected shell title.
                    if expected and w['hwnd'] not in getattr(self, '_before_hwnds', set()) and ('powershell' in w['title'].casefold() or 'pwsh' in w['title'].casefold()):
                        for p in psutil.process_iter(['name', 'exe']):
                            try:
                                if p.pid not in self._before_pids and p.info['name'].casefold() == expected:
                                    if not entry.executable or os.path.normcase(p.info['exe']) == os.path.normcase(entry.executable):
                                        match = True
                                        w['shell_process_id'] = p.pid
                                        break
                            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
                if match:
                    # Explorer desktop/taskbar are not a file-manager window.
                    from .app_resolver import canonical
                    if canonical(entry.name) == 'explorer' or Path(entry.executable).name.casefold() == 'explorer.exe':
                        import ctypes
                        buf = ctypes.create_unicode_buffer(256)
                        ctypes.windll.user32.GetClassNameW(w['hwnd'], buf, 256)
                        if buf.value not in {'CabinetWClass', 'ExploreWClass'}: continue
                    candidates.append(w)
            for w in candidates:
                try:
                    focus_window_handle(w['hwnd'])
                    observed = window_metadata(w['hwnd'])
                    if observed.get('process_id') != w.get('process_id') or observed.get('executable') != w.get('executable'):
                        raise LookupError('Window identity changed during focus')
                    if w.get('shell_process_id'): observed['shell_process_id'] = w['shell_process_id']
                    if observed.get('hwnd') and observed.get('process_id') and observed.get('monitor_id'):
                        return observed
                except (OSError, LookupError): pass
            sleep(.1)
        raise TimeoutError('Окно приложения не подтверждено')

    def open(self, name: str, cancellation_check=None) -> dict[str, Any]:
        attempts = []
        for refresh in (False, True):
            if cancellation_check: cancellation_check()
            try: entries = self.resolver.resolve(name, refresh=refresh)
            except LookupError:
                if refresh: raise
                continue
            for entry in entries:
                if cancellation_check: cancellation_check()
                try:
                    pid = self._launch(entry)
                    window = self._verify(entry, pid, cancellation_check)
                    from .app_resolver import canonical
                    display_name = {'obsidian': 'Obsidian', 'vscode': 'VS Code', 'visualstudio': 'Visual Studio'}.get(canonical(entry.name), entry.name)
                    return {'opened': display_name, 'name': display_name, 'requested_name': name, 'executable': window['executable'],
                        'process_id': window['process_id'], 'hwnd': window['hwnd'],
                        'monitor_index': window['monitor_index'], 'window': window,
                        'verified': True, 'method': entry.kind, 'attempts': attempts}
                except (OSError, LookupError, ValueError) as exc:
                    if cancellation_check: cancellation_check()
                    attempts.append({'method': entry.kind, 'target': entry.target, 'error': str(exc)})
        raise OSError('Не удалось подтвердить окно «'+name+'»: '+str(attempts))
