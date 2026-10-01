"""Actions on existing typed resolver results, with identity read-back."""
from pathlib import Path
import os
from time import monotonic, sleep

from .shell_metadata import explorer_locations, open_folder_verified, timestamp


class DesktopTargetActions:
    def __init__(self, registry, resolver):
        self.registry, self.resolver = registry, resolver

    def _clarify(self, name, type, location, candidates, question):
        from .app_resolver import TargetClarificationRequired
        cancellation = getattr(self.registry, 'cancellation', None)
        token = cancellation.get() if cancellation is not None else None
        if token is not None:
            # The registry enforces this across *all* later mutation tools;
            # the planner cannot switch to open_folder to bypass a question.
            token.target_confirmation_required = dict(name=name, type=type,
                location=location, candidates=candidates, question=question)
        raise TargetClarificationRequired(question)

    def _require_clarification(self, result, name, type, location):
        if not result['found']:
            nearby = result.get('near_candidates', [])
            if nearby:
                if len(nearby) == 1 and type == 'folder' and location.casefold() in {'desktop', 'downloads'}:
                    place = 'На рабочем столе' if location.casefold() == 'desktop' else 'В загрузках'
                    question = place + ' нашла похожую папку «' + nearby[0]['name'] + '». Открыть её?'
                    self._clarify(name, type, location, nearby, question)
                choices = ', '.join('«' + item['name'] + '» (' + item['path'] + ')' for item in nearby[:5])
                self._clarify(name, type, location, nearby,
                    'Точного имени нет. Нашла похожее: ' + choices + '. Открыть это расположение?')
        if result['ambiguous']:
            choices = ', '.join(item['path'] or item['name'] for item in result['items'][:5])
            self._clarify(name, type, location, result['items'],
                'Найдено несколько совпадений. Какое расположение открыть: ' + choices + '?')

    def resolve(self, name, type='application', location=''):
        from .app_resolver import TargetClarificationRequired
        result = self.resolver.resolve_target(name, type, location, refresh=True)
        try:
            self._require_clarification(result, name, type, location)
        except TargetClarificationRequired as exc:
            # Preserve read-only discovery metadata while binding the same
            # task's later actions. resolve(near) -> open(candidate) is not
            # permission to substitute the user's requested target.
            return dict(result, status='needs_clarification', question=exc.question)
        return result

    def open(self, name, type='application', location=''):
        from .app_resolver import TargetClarificationRequired
        result = self.resolve(name, type, location)
        if result.get('status') == 'needs_clarification':
            raise TargetClarificationRequired(result['question'])
        if not result['found']:
            raise LookupError(f'Не найден {type}: {name}. ' + '; '.join(result['limitations']))
        target = result['items'][0]
        self.registry.check_cancel()
        if type == 'application':
            opened = self.registry.execute('open_application', {'name': name})
            if not opened.ok: raise RuntimeError(opened.error)
            return dict(opened.data, target=target, coverage=result)
        if type == 'folder':
            opened = open_folder_verified(target['path'], self.registry.check_cancel)
            return dict(opened, target=target, resolution=result)
        if type == 'window':
            from .window_control import WindowController
            current = WindowController(self.registry).target(target['hwnd'])
            if current.get('process_id') != target['process_id'] or current.get('executable') != target['executable']:
                raise LookupError('Окно изменилось после поиска')
            return dict(WindowController(self.registry).change('focus', target['hwnd']), target=target, coverage=result)
        if type in {'file', 'image', 'document'}:
            path = Path(target['path'])
            if not path.is_file():
                self.resolver.invalidate_metadata(str(path))
                raise FileNotFoundError(path)
            os.startfile(str(path))
            return dict(verified=False, requested=True, target=target, coverage=result,
                reason='Открытие файла запрошено; документ в приложении ещё не подтверждён')
        if type == 'virtual_shell_item':
            path = target['path']
            # Opening a namespace never implies emptying Recycle Bin.
            if not path.startswith('::') and not path.startswith('shell:'):
                raise ValueError('Shell item has no verified namespace identifier')
            os.startfile('shell:' + path if path.startswith('::') else path)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                self.registry.check_cancel()
                windows = explorer_locations()
                matches = [item for item in windows['items'] if item.get('path', '').casefold() == path.casefold()]
                if matches:
                    from .windows import focus_window_handle
                    focused = focus_window_handle(matches[0]['hwnd'])
                    return dict(verified=True, target=target, location=matches[0], coverage=windows,
                        hwnd=focused['hwnd'], window=focused, process_id=focused.get('process_id'),
                        executable=focused.get('executable'), monitor_index=focused.get('monitor_index'))
                sleep(.15)
            return dict(verified=False, requested=True, target=target,
                reason='Shell item запрошен, но расположение не подтверждено')
        if type == 'webpage':
            opened = self.registry.execute('open_url', {'url': target['path']})
            if not opened.ok: raise RuntimeError(opened.error)
            return dict(opened.data, target=target, coverage=result)
        raise ValueError('Для этой цели нет подтверждённого действия: ' + type)

    def show_explorer_locations(self):
        from .windows import focus_window_handle
        snapshot = explorer_locations()
        items = snapshot['items']
        if not items:
            return dict(snapshot, verified=False, reason='Открытые расположения Проводника не обнаружены; скрытые вкладки не проверены')
        # Preserve all observed locations in the response, focus an existing
        # window only. No os.startfile/explorer launch occurs in this action.
        self.registry.check_cancel()
        focused = focus_window_handle(items[0]['hwnd'])
        return dict(snapshot, verified=True, focused=focused, reused=True, hwnd=focused['hwnd'],
                    window=focused, process_id=focused.get('process_id'),
                    executable=focused.get('executable'), monitor_index=focused.get('monitor_index'))

    def close_application(self, query, scope='application'):
        from .window_control import WindowController
        from .native import window_metadata
        if scope not in {'application', 'window'}:
            raise ValueError('scope must be application or window')
        candidates = self.resolver.resolve_windows(query)
        if not candidates:
            raise LookupError('Нет открытого окна приложения «' + query + '» с подтверждённым executable')
        if scope == 'window' and len(candidates) != 1:
            self._clarify(query, 'window', 'desktop_session', [dict(w, type='window', name=w['title'],
                identity=f"window:{w['hwnd']}:{w.get('process_id', 0)}", source='EnumWindows + executable',
                verified_at=timestamp()) for w in candidates],
                'Какое окно закрыть: ' + ', '.join(w['title'] for w in candidates[:5]) + '?')
        controller, results = WindowController(self.registry), []
        for candidate in candidates:
            self.registry.check_cancel()
            actual = window_metadata(candidate['hwnd'])
            if actual.get('process_id') != candidate.get('process_id') or actual.get('executable') != candidate.get('executable'):
                raise LookupError('Window identity changed before closing')
            # WM_CLOSE only. A save prompt is returned as an incomplete action;
            # there is no terminate/kill fallback, including for other windows.
            result = controller.change('close', candidate['hwnd'])
            results.append(result)
            if not result.get('verified'):
                break
        from .app_resolver import canonical
        label = {'vscode': 'VS Code', 'visualstudio': 'Visual Studio', 'obsidian': 'Obsidian'}.get(canonical(query), query)
        return dict(verified=bool(results) and len(results) == len(candidates) and all(r.get('verified') for r in results),
            closed=bool(results) and all(r.get('closed') for r in results), close_requested=True,
            results=results, scope=scope, name=label,
            target=dict(type='application' if scope == 'application' else 'window', name=label,
                identity='executable:' + candidates[0]['executable'], source='EnumWindows + executable',
                verified_at=timestamp(), scope=scope),
            reason='' if all(r.get('verified') for r in results) else 'Окно осталось открытым; проверь диалог сохранения')


def register_desktop_target_tools(registry, resolver, uia):
    from kk.tools.registry import Tool
    from kk.permissions.policy import PermissionLevel as P
    from .factory import obj
    from .app_resolver import TARGET_TYPES
    actions = DesktopTargetActions(registry, resolver)
    s = {'type': 'string'}
    target = dict(name=s, type={'type': 'string', 'enum': sorted(TARGET_TYPES)}, location=s)
    registry.register(Tool('resolve_desktop_target',
        'Resolve an explicitly typed target. Folder/file names never become apps. Returns identities, sources, freshness and coverage.',
        obj(target, ['name', 'type']), P.READ, actions.resolve))
    registry.register(Tool('open_desktop_target',
        'Open a typed desktop target and verify identity. Explicit Desktop/Downloads location overrides app similarity.',
        obj(target, ['name', 'type']), P.ACTION, actions.open))
    registry.register(Tool('list_explorer_locations',
        'Observe existing Explorer HWNDs and directories. Reports partial coverage when hidden tabs cannot be observed.',
        obj(), P.READ, explorer_locations))
    registry.register(Tool('show_explorer_locations',
        'Show an existing Explorer window and return observed directories without creating duplicate windows.',
        obj(), P.ACTION, actions.show_explorer_locations))
    registry.register(Tool('close_application',
        'Normal close of observed executable-identical app windows; no title substring, no app launch or process kill. Save dialogs remain open.',
        obj(dict(query=s, scope={'type': 'string', 'enum': ['application', 'window']}), ['query']),
        P.ACTION, actions.close_application))
    from .obsidian import ObsidianDrafts
    drafts = ObsidianDrafts(registry, uia)
    registry.register(Tool('open_obsidian_draft',
        'Create a fresh safe Obsidian note, open its editor, insert EXACT payload and verify the saved text. Never replaces an existing note or executes payload.',
        obj(dict(text=s), ['text']), P.ACTION, drafts.create))
