"""Exact dictated text in a new, exclusively created Obsidian draft."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
from time import monotonic, sleep
from urllib.parse import urlencode, quote
from uuid import uuid4


class ObsidianDrafts:
    def __init__(self, registry, uia):
        self.registry, self.uia = registry, uia

    @staticmethod
    def _editor(field):
        return (not field.get('password') and not field.get('offscreen') and
                field.get('control_type') in {'EditControl', 'DocumentControl'} and
                'cm-content' in field.get('class_name', '').split() and
                ('value' in field or 'text' in field))

    def _vault(self, window):
        config = Path(os.environ.get('APPDATA', '')) / 'obsidian' / 'obsidian.json'
        if not config.is_file() or config.stat().st_size > 1_000_000:
            raise LookupError('Локальный список хранилищ Obsidian не найден')
        vaults = json.loads(config.read_text(encoding='utf-8')).get('vaults', {})
        candidates = [Path(row['path']) for row in vaults.values() if row.get('open') and
                      row.get('path') and Path(row['path']).is_dir()]
        if len(candidates) > 1:
            title = window.get('title', '').casefold()
            candidates = [path for path in candidates if path.name.casefold() in title]
        if len(candidates) != 1:
            raise LookupError('Не удалось однозначно определить открытое хранилище Obsidian')
        return candidates[0]

    def create(self, text):
        from .native import foreground_window
        from .shell_metadata import timestamp
        if not text:
            raise ValueError('Текст заметки пуст')
        opened = self.registry.execute('open_application', {'name': 'Obsidian'})
        if not opened.ok:
            raise RuntimeError(opened.error)
        window = opened.data.get('window', opened.data)
        if not opened.data.get('verified') or Path(window.get('executable', '')).name.casefold() != 'obsidian.exe':
            raise LookupError('Окно Obsidian не подтверждено')
        vault = self._vault(window)
        filename = 'Onyx draft ' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:8]
        path = vault / (filename + '.md')
        self.registry.check_cancel()
        # Exclusive creation proves this is a new blank note. No overwrite of
        # existing notes and no selection-all operation are possible here.
        with path.open('x', encoding='utf-8', newline=''):
            pass
        # Obsidian parses URI parameters as encodeURIComponent, not HTML form
        # data: a plus in a Windows path is literal, not an encoded space.
        uri = 'obsidian://open?' + urlencode({'path': str(path)}, quote_via=quote)
        os.startfile(uri)
        deadline = monotonic() + 7
        editable = False
        while monotonic() < deadline:
            self.registry.check_cancel()
            current = foreground_window()
            if current.get('hwnd') == window['hwnd'] and current.get('process_id') == window['process_id']:
                _, field = self.uia.focused()
                content = str(field.get('value') or field.get('text') or '')
                # URI dispatch alone is not evidence of which note is active.
                # Require the uniquely generated name and an empty editable.
                if (filename.casefold() in current.get('title', '').casefold() and
                        not content.strip() and self._editor(field)):
                    editable = True
                    break
            sleep(.15)
        target = dict(type='document', name=path.name, path=str(path), identity='file:' + os.path.normcase(str(path)),
                      source='Obsidian active vault + exclusive draft', scope=str(vault), verified_at=timestamp())
        if not editable:
            return dict(verified=False, target=target, draft_created=True, typed=False,
                reason='Создан пустой черновик; его активное редактируемое поле не подтверждено, текст не введён')
        def target_guard():
            current = foreground_window()
            _, field = self.uia.focused()
            if (current.get('hwnd') != window['hwnd'] or current.get('process_id') != window['process_id'] or
                    filename.casefold() not in current.get('title', '').casefold() or not self._editor(field)):
                raise InterruptedError('Активная заметка или редактор Obsidian изменились; ввод остановлен')
        target_guard()
        result = self.registry.text_entry.enter(text, submit=False, target_guard=target_guard)
        deadline = monotonic() + 5
        while monotonic() < deadline:
            self.registry.check_cancel()
            # The exact new file gives independent read-back, even when the
            # accessibility API cannot expose the full editor text.
            if path.read_text(encoding='utf-8') == text:
                return dict(verified=True, target=target, typed=True, submitted=False,
                    characters=len(text), file_readback=True, editor_result=result)
            sleep(.15)
        return dict(verified=False, target=target, typed=True, submitted=False, editor_result=result,
            reason='Ввод выполнен, но точное содержимое черновика не подтверждено. Повторять ввод нельзя')
