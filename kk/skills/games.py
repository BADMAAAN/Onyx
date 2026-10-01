"""Data-driven launcher skills with bounded UIA and process verification."""
import json
import os
import subprocess
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit


class GameRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.games = {'hearthstone': dict(game='Hearthstone', launcher='Battle.net',
            method='uia', process='Hearthstone.exe', play_labels=['Играть', 'Play', 'Launch'])}
        if self.path.exists(): self.games.update(json.loads(self.path.read_text(encoding='utf-8')))

    def resolve(self, game):
        return self.games.get(game.casefold().strip(' .!?'))

    def remember(self, game, launcher):
        entry = dict(self.resolve(game) or dict(game=game, process=game+'.exe', play_labels=['Играть','Play','Launch']))
        entry.update(launcher=launcher, method='uia')
        entry.pop('uri', None)
        entry.pop('arguments', None)
        self.games[game.casefold()] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.games, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)
        return entry


class GameLauncher:
    def __init__(self, games, launcher, check):
        self.games, self.launcher, self.check = games, launcher, check

    def running(self, entry):
        from kk.tools.windows.windows import list_visible_windows
        return next((w for w in list_visible_windows()
                     if w.get('process','').casefold() == entry['process'].casefold()), None)

    def open_launcher(self, name):
        from kk.tools.windows.windows import list_visible_windows, focus_window_handle
        entries = self.launcher.resolver.resolve(name)
        folders = {str(Path(e.executable).parent).casefold() for e in entries if e.executable}
        # Self-updating launchers can run a temporary .exe in their install directory.
        windows = [w for w in list_visible_windows() if w.get('title','').casefold() == name.casefold()
                   and str(Path(w.get('executable','')).parent).casefold() in folders]
        if len(windows) == 1:
            self.check()
            focus_window_handle(windows[0]['hwnd'])
            return windows[0]
        return self.launcher.open(name, self.check)

    @staticmethod
    def matches_control(name, control_type, labels, game=None):
        if game:
            return control_type == 'ButtonControl' and any(
                name.casefold() == label.casefold() or
                name.casefold().startswith(label.casefold()+': '+game.casefold()+',')
                for label in labels)
        return control_type in {'TabItemControl','ButtonControl','ListItemControl'} and name.casefold().strip() in {s.casefold() for s in labels}

    def invoke(self, hwnd, labels, timeout=8, game=None):
        import uiautomation as auto
        deadline = monotonic()+timeout
        while monotonic() < deadline:
            self.check()
            root = auto.ControlFromHandle(hwnd)
            queue, matches, count = [root], [], 0
            while queue and count < 800 and monotonic() < deadline:
                self.check()
                control = queue.pop(0); count += 1
                if self.matches_control(control.Name, control.ControlTypeName, labels, game) and control.IsEnabled and not control.IsOffscreen:
                    matches.append(control)
                queue.extend(control.GetChildren())
            if len(matches) == 1:
                control = matches[0]
                try: control.GetInvokePattern().Invoke()
                except Exception: control.GetSelectionItemPattern().Select()
                return
            sleep(.2)
        raise RuntimeError('Не найден однозначный UIA-элемент: '+', '.join(labels))

    def launch(self, game):
        entry = self.games.resolve(game)
        if not entry: raise RuntimeError('Через какой launcher запускать '+game+'?')
        window = self.running(entry)
        if window: return dict(verified=True, already_running=True, window=window)
        self.check()
        # A configured direct method is preferred. Defaults use observed UIA;
        # undocumented launcher protocols are never guessed from game names.
        method = entry.get('method', 'uia')
        if method == 'uri':
            uri = entry['uri']
            if urlsplit(uri).scheme not in {'steam', 'battlenet', 'com.epicgames.launcher'}:
                raise ValueError('Unsupported launcher protocol')
            os.startfile(uri)
        elif method == 'arguments':
            candidate = next((e for e in self.launcher.resolver.resolve(entry['launcher']) if e.kind == 'executable'), None)
            if not candidate: raise RuntimeError('Launcher executable not found')
            subprocess.Popen([candidate.target, *entry['arguments']], creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            opened = self.open_launcher(entry['launcher'])
            self.invoke(opened['hwnd'], [entry['game']])
            self.check()
            self.invoke(opened['hwnd'], entry['play_labels'], game=entry['game'])
        deadline = monotonic()+25
        while monotonic() < deadline:
            self.check()
            window = self.running(entry)
            if window: return dict(verified=True, method=method, window=window)
            sleep(.2)
        raise RuntimeError('Окно игры не появилось; запуск не подтверждён')
