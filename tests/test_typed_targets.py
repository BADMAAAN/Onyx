"""Typed collisions, stale metadata, native identity and exact safe drafts."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
import logging
from time import monotonic

from kk.tools.observation import observation
from kk.tools.registry import Tool, ToolRegistry, ToolResult
from kk.permissions.policy import PermissionLevel
from kk.tools.windows.app_resolver import ApplicationResolver, canonical
from kk.tools.windows.desktop_targets import DesktopTargetActions
from kk.tools.windows.files import directory_observation, list_downloads
from kk.tools.windows.obsidian import ObsidianDrafts


class TypedResolutionTests(TestCase):
    def test_app_folder_file_collision_uses_explicit_type_and_location(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'Discord').mkdir()
            (root / 'Discord.png').write_bytes(b'fixture')
            (root / 'Discord.exe').write_bytes(b'fixture')
            resolver = ApplicationResolver()
            resolver.resolve = Mock(side_effect=AssertionError('App discovery forbidden for explicit files'))
            folder = resolver.resolve_target('Discord', 'folder', temp)
            image = resolver.resolve_target('Discord.png', 'image', temp)
            self.assertEqual(folder['items'][0]['path'], str(root / 'Discord'))
            self.assertEqual(image['items'][0]['path'], str(root / 'Discord.png'))
            self.assertFalse(resolver.resolve_target('Discord.png', 'folder', temp)['found'])
            self.assertFalse(resolver.resolve_target('Discord', 'file', temp)['found'])
            self.assertNotEqual(folder['items'][0]['identity'], image['items'][0]['identity'])
            self.assertTrue(folder['items'][0]['verified_at'])

    def test_onyx_name_is_not_agent_project_or_agent_ui(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'Оникс').mkdir()
            resolver = ApplicationResolver()
            result = resolver.resolve_target('Onyx', 'folder', temp)
            self.assertEqual(result['items'][0]['path'], str(root / 'Оникс'))
            self.assertEqual(result['items'][0]['type'], 'folder')
            agent = resolver.resolve_target('Onyx', 'agent_ui')
            self.assertFalse(agent['found'])
            self.assertEqual(agent['completeness'], 'unknown')

    def test_removed_entry_invalidated_before_action(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'Onyx'
            path.mkdir()
            resolver = ApplicationResolver()
            self.assertTrue(resolver.resolve_target('Onyx', 'folder', temp)['found'])
            path.rmdir()
            result = resolver.resolve_target('Onyx', 'folder', temp)
            self.assertFalse(result['found'])
            self.assertIn('Stale entry', str(result['limitations']))
            self.assertFalse(resolver.metadata_cache)

    def test_multiple_real_locations_are_ambiguous(self):
        with TemporaryDirectory() as temp:
            paths = [Path(temp) / 'user' / 'Onyx', Path(temp) / 'common' / 'Onyx']
            for path in paths: path.mkdir(parents=True)
            resolver = ApplicationResolver()
            resolver.metadata = Mock(return_value=observation([
                dict(name='Onyx', path=str(p), type='folder', source='KnownFolder:desktop') for p in paths],
                source='Known Folders', scope='desktop', completeness='complete'))
            result = resolver.resolve_target('Onyx', 'folder', 'desktop')
            self.assertTrue(result['ambiguous'])
            tools = SimpleNamespace(check_cancel=Mock())
            with self.assertRaisesRegex(LookupError, 'Какое расположение'):
                DesktopTargetActions(tools, resolver).open('Onyx', 'folder', 'desktop')

    def test_shortcut_resolves_target_folder_without_becoming_application(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'actual'
            folder.mkdir()
            resolver = ApplicationResolver()
            resolver.metadata = Mock(return_value=observation([
                dict(name='Onyx', path=str(root / 'Onyx.lnk'), target_path=str(folder),
                     type='folder', is_shortcut=True, source='Shell Desktop')],
                source='Shell', scope='desktop', completeness='complete'))
            self.assertEqual(resolver.resolve_target('Onyx', 'folder', 'desktop')['items'][0]['path'], str(folder))

    def test_browser_tab_requires_observed_identity(self):
        result = ApplicationResolver().resolve_target('YouTube', 'browser_tab')
        self.assertFalse(result['found'])
        self.assertEqual(result['completeness'], 'unknown')
        self.assertIn('tabId', str(result['limitations']))

    def test_recycle_context_alias_does_not_cover_generic_trash(self):
        resolver = ApplicationResolver()
        path = '::{645FF040-5081-101B-9F08-00AA002F954E}'
        resolver.metadata = Mock(return_value=observation([
            dict(name='Корзина', path=path, type='virtual_shell_item', source='Shell Desktop')],
            source='Shell', scope='desktop', completeness='complete'))
        self.assertTrue(resolver.resolve_target('мусорку', 'virtual_shell_item')['found'])
        self.assertFalse(resolver.resolve_target('мусор', 'virtual_shell_item')['found'])
        self.assertFalse(resolver.resolve_target('вынеси мусор', 'virtual_shell_item')['found'])

    def test_missing_onyx_never_substitutes_project(self):
        with TemporaryDirectory() as temp:
            self.assertFalse(ApplicationResolver().resolve_target('Onyx', 'folder', temp)['found'])

    def test_explicit_absolute_path_has_own_scope_without_folder_discovery(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'Onyx'
            path.mkdir()
            resolver = ApplicationResolver()
            resolver.metadata = Mock(side_effect=AssertionError('No unrelated discovery for explicit path'))
            result = resolver.resolve_target(str(path), 'folder', 'desktop')
            self.assertTrue(result['found'])
            self.assertEqual(result['source'], 'explicit_path')
            self.assertEqual(result['scope'], str(path))
            self.assertEqual(result['completeness'], 'complete')
            self.assertEqual(result['items'][0]['scope'], str(path))
            self.assertEqual(result['items'][0]['source'], 'explicit_path')
            resolver.metadata.assert_not_called()

    def test_document_type_does_not_open_executables_or_scripts(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('Discord.exe', 'Discord.ps1', 'Discord.md'):
                (root / name).write_text('fixture', encoding='utf-8')
            resolver = ApplicationResolver()
            self.assertFalse(resolver.resolve_target('Discord.exe', 'document', temp)['found'])
            self.assertFalse(resolver.resolve_target('Discord.ps1', 'document', temp)['found'])
            self.assertTrue(resolver.resolve_target('Discord.md', 'document', temp)['found'])
            self.assertTrue(resolver.resolve_target('Discord.exe', 'file', temp)['found'])
            with patch('os.startfile') as launch:
                with self.assertRaises(LookupError):
                    DesktopTargetActions(SimpleNamespace(check_cancel=Mock()), resolver).open('Discord.exe', 'document', temp)
            launch.assert_not_called()

    def test_missing_absolute_path_does_not_scan_for_replacement(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'missing' / 'Onyx'
            resolver = ApplicationResolver()
            resolver.metadata = Mock(side_effect=AssertionError('Missing absolute path is not a search'))
            result = resolver.resolve_target(str(path), 'folder', 'desktop')
            self.assertFalse(result['found'])
            self.assertEqual(result['source'], 'explicit_path')
            self.assertEqual(result['scope'], str(path))
            self.assertEqual(result['completeness'], 'complete')
            self.assertEqual(result['items'], [])
            self.assertEqual(result['near_candidates'], [])
            resolver.metadata.assert_not_called()

    def test_adjacent_spelling_error_needs_confirmation_before_action(self):
        with TemporaryDirectory() as temp:
            (Path(temp) / 'онкис').mkdir()
            resolver = ApplicationResolver()
            result = resolver.resolve_target('Onyx', 'folder', temp)
            self.assertFalse(result['found'])
            self.assertTrue(result['near_candidates'][0]['needs_confirmation'])
            with patch('os.startfile') as launch:
                with self.assertRaisesRegex(LookupError, 'Открыть это расположение'):
                    DesktopTargetActions(SimpleNamespace(check_cancel=Mock()), resolver).open('Onyx', 'folder', temp)
            launch.assert_not_called()


class MetadataCoverageTests(TestCase):
    def test_collection_limit_reports_partial(self):
        with TemporaryDirectory() as temp:
            for i in range(501): (Path(temp) / f'{i:04}.txt').touch()
            snapshot = directory_observation(temp)
            self.assertEqual(len(snapshot['items']), 500)
            self.assertEqual(snapshot['completeness'], 'partial')
            self.assertIn('500', str(snapshot['limitations']))

    def test_metadata_does_not_read_content_or_invent_download_date(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)
            (path / 'fixture.txt').write_text('private contents', encoding='utf-8')
            with patch('kk.tools.windows.files.downloads_folder', return_value=path), patch.object(Path, 'read_text') as read:
                result = list_downloads()
            read.assert_not_called()
            self.assertEqual(result['completeness'], 'complete')
            self.assertFalse(result['contents_read'])
            self.assertNotIn('downloaded_at', result['entries'][0])
            self.assertIn('not download time', result['date_meaning'])

    def test_existing_explorer_focus_does_not_launch_duplicates(self):
        snapshot = observation([dict(hwnd=77, path='C:/fixture')], source='Shell', scope='explorer',
            completeness='partial', limitations=['Hidden tabs unavailable'])
        actions = DesktopTargetActions(SimpleNamespace(check_cancel=Mock()), Mock())
        with patch('kk.tools.windows.desktop_targets.explorer_locations', return_value=snapshot), \
             patch('kk.tools.windows.windows.focus_window_handle', return_value={'hwnd':77}) as focus, \
             patch('os.startfile') as launch:
            result = actions.show_explorer_locations()
        launch.assert_not_called()
        focus.assert_called_once_with(77)
        self.assertEqual(result['completeness'], 'partial')

    def test_verified_folder_returns_native_window_identity_for_next_command(self):
        from kk.tools.windows.shell_metadata import open_folder_verified
        with TemporaryDirectory() as temp:
            window = dict(hwnd=77, process_id=9, process='explorer.exe', executable='C:/Windows/explorer.exe', monitor_index=2)
            snapshot = observation([dict(hwnd=77, path=temp)], source='Shell', scope='explorer',
                completeness='partial', limitations=['Hidden tabs unavailable'])
            with patch('kk.tools.windows.shell_metadata.explorer_locations', return_value=snapshot), \
                 patch('kk.tools.windows.windows.focus_window_handle', return_value=window), patch('os.startfile') as launch:
                result = open_folder_verified(temp)
            launch.assert_not_called()
            self.assertEqual(result['window'], window)
            self.assertEqual(result['process_id'], 9)
            self.assertEqual(result['hwnd'], 77)


class TypedContextAndClarificationTests(TestCase):
    def registry(self):
        return ToolRegistry(Mock(), Mock(), logging.getLogger('typed_context'))

    def test_imperative_is_not_implicit_alias_but_explicit_learning_survives(self):
        from kk.core.personal_intents import PersonalIntentRegistry
        intents = PersonalIntentRegistry()
        for text in ('закрой это окно', 'сделай это окно full', 'открой файл X = Y'):
            self.assertIsNone(intents.learning(text), text)
        self.assertEqual(intents.learning('Когда я говорю "закрой", открывай Discord')['phrase'], 'закрой')
        self.assertEqual(intents.learning('под "мой редактор" я имею в виду VS Code')['entry']['target'], 'VS Code')
        self.assertEqual(intents.learning('DS это Discord')['entry']['target'], 'Discord')

    def test_opened_folder_and_shown_explorer_replace_old_app_referent(self):
        from kk.core.decision_policy import OperationalContext
        from kk.core.fast_commands import run_fast
        from kk.core.state import StateMachine
        from kk.core.task import AgentTask
        for tool_name in ('open_desktop_target', 'show_explorer_locations'):
            with self.subTest(tool=tool_name):
                tools = self.registry()
                tools.desktop_context = OperationalContext('Obsidian', 101, monotonic(), 1001)
                window = dict(hwnd=202, process_id=2002, process='explorer.exe', executable='C:/Windows/explorer.exe', monitor_index=2)
                result = dict(verified=True, hwnd=202, window=window, target={'type':'folder', 'name':'Fixture'})
                tools.register(Tool(tool_name, '', {'type':'object'}, PermissionLevel.ACTION, Mock(return_value=result)))
                closed = Mock(return_value={'verified':True, 'closed':True, 'hwnd':202})
                tools.register(Tool('close_window', '', {'type':'object', 'properties':{'hwnd':{'type':'integer'}}},
                    PermissionLevel.ACTION, closed))
                self.assertTrue(tools.execute(tool_name).ok)
                answer = run_fast('закрой это окно', AgentTask('закрой это окно'), tools, StateMachine())
                self.assertEqual(answer, 'Закрыла.')
                closed.assert_called_once_with(hwnd=202)
                self.assertEqual(tools.desktop_context.process_id, 2002)
                self.assertEqual(tools.desktop_context.last_entity, 'Fixture')

    def test_ambiguous_single_window_close_latches_later_mutations(self):
        from kk.core.task import CancellationToken
        tools = self.registry()
        windows = [dict(hwnd=i, process_id=i + 10, title=f'Code {i}', executable='C:/Apps/Code.exe') for i in (1, 2)]
        resolver = SimpleNamespace(resolve_windows=Mock(return_value=windows))
        actions = DesktopTargetActions(tools, resolver)
        tools.register(Tool('close_application', '', {'type':'object', 'properties':{
            'query':{'type':'string'}, 'scope':{'type':'string'}}}, PermissionLevel.ACTION, actions.close_application))
        unrelated = Mock()
        tools.register(Tool('mutation_fixture', '', {'type':'object'}, PermissionLevel.ACTION, unrelated))
        token = CancellationToken()
        token.user_request = 'закрой окно VS Code'
        context = tools.cancellation.set(token)
        try:
            result = tools.execute('close_application', {'query':'VS Code', 'scope':'window'})
            self.assertFalse(result.ok)
            self.assertEqual(result.data['status'], 'needs_clarification')
            self.assertEqual(token.target_confirmation_required['type'], 'window')
            self.assertFalse(tools.execute('mutation_fixture').ok)
            unrelated.assert_not_called()
        finally:
            tools.cancellation.reset(context)

    def test_read_resolution_cannot_authorize_near_candidate_substitution(self):
        from kk.core.task import CancellationToken
        with TemporaryDirectory() as temp:
            (Path(temp) / 'онкис').mkdir()
            tools = self.registry()
            actions = DesktopTargetActions(tools, ApplicationResolver())
            schema = {'type':'object', 'properties':{'name':{'type':'string'},
                'type':{'type':'string'}, 'location':{'type':'string'}}}
            tools.register(Tool('resolve_desktop_target', '', schema, PermissionLevel.READ, actions.resolve))
            tools.register(Tool('open_desktop_target', '', schema, PermissionLevel.ACTION, actions.open))
            fallback = Mock()
            tools.register(Tool('open_folder', '', {'type':'object'}, PermissionLevel.ACTION, fallback))
            token = CancellationToken()
            token.user_request = 'не закрывай VS Code, открой папку Onyx'
            context = tools.cancellation.set(token)
            try:
                resolved = tools.execute('resolve_desktop_target', {'name':'Onyx', 'type':'folder', 'location':temp})
                self.assertTrue(resolved.ok)
                self.assertEqual(resolved.data['status'], 'needs_clarification')
                self.assertFalse(resolved.data['found'])
                self.assertEqual(resolved.data['near_candidates'][0]['name'], 'онкис')
                self.assertTrue(token.target_confirmation_required)
                with patch('kk.tools.windows.desktop_targets.open_folder_verified') as native:
                    result = tools.execute('open_desktop_target', {'name':'онкис', 'type':'folder', 'location':temp})
                    self.assertFalse(result.ok)
                    self.assertEqual(result.data['status'], 'needs_clarification')
                    native.assert_not_called()
                self.assertFalse(tools.execute('open_folder').ok)
                fallback.assert_not_called()
            finally:
                tools.cancellation.reset(context)


class ExactApplicationIdentityTests(TestCase):
    def test_code_executable_beats_title_and_visual_studio(self):
        windows = [dict(hwnd=1, title='VS Code', executable='C:/Windows/pwsh.exe'),
                   dict(hwnd=2, title='VS Code', executable='C:/Apps/devenv.exe'),
                   dict(hwnd=3, title='notes', executable='C:/Apps/Code.exe')]
        resolver = ApplicationResolver()
        with patch('kk.tools.windows.windows.list_visible_windows', return_value=windows):
            self.assertEqual([w['hwnd'] for w in resolver.resolve_windows('ви эс код')], [3])
            self.assertEqual([w['hwnd'] for w in resolver.resolve_windows('Visual Studio')], [2])
        self.assertEqual(canonical('Обсидиан'), 'obsidian')

    def test_save_dialog_stops_close_without_kill_or_launch(self):
        candidate = dict(hwnd=3, title='fixture', executable='C:/Apps/Code.exe', process_id=8)
        resolver = SimpleNamespace(resolve_windows=Mock(return_value=[candidate, dict(candidate, hwnd=4)]))
        registry = SimpleNamespace(check_cancel=Mock(), execute=Mock())
        actions = DesktopTargetActions(registry, resolver)
        with patch('kk.tools.windows.native.window_metadata', return_value=candidate), \
             patch('kk.tools.windows.window_control.WindowController') as controller:
            controller.return_value.change.return_value = dict(verified=False, close_requested=True)
            result = actions.close_application('VS Code')
        self.assertFalse(result['verified'])
        self.assertIn('сохранения', result['reason'])
        controller.return_value.change.assert_called_once_with('close', 3)
        registry.execute.assert_not_called()

    def test_recycled_hwnd_is_rejected(self):
        candidate = dict(hwnd=3, title='fixture', executable='C:/Apps/Code.exe', process_id=8)
        actions = DesktopTargetActions(SimpleNamespace(check_cancel=Mock()), SimpleNamespace(resolve_windows=lambda _: [candidate]))
        with patch('kk.tools.windows.native.window_metadata', return_value=dict(candidate, process_id=9)), \
             patch('kk.tools.windows.window_control.WindowController') as controller:
            with self.assertRaisesRegex(LookupError, 'identity'):
                actions.close_application('VS Code')
        controller.return_value.change.assert_not_called()


class ObsidianDraftTests(TestCase):
    def setup_draft(self, temp):
        window = dict(hwnd=4, process_id=5, executable='C:/Apps/Obsidian.exe', title='vault - Obsidian')
        tools = SimpleNamespace(check_cancel=Mock(), execute=Mock(return_value=ToolResult(True, dict(verified=True, window=window))),
            text_entry=SimpleNamespace(enter=Mock()))
        uia = SimpleNamespace(focused=Mock(return_value=(None, {'control_type':'EditControl', 'class_name':'cm-content cm-lineWrapping', 'text':''})))
        draft = ObsidianDrafts(tools, uia)
        draft._vault = Mock(return_value=Path(temp))
        return draft, tools, window

    def test_fresh_draft_exact_payload_and_existing_note_preserved(self):
        with TemporaryDirectory() as temp:
            existing = Path(temp) / 'existing.md'
            existing.write_text('Keep this note', encoding='utf-8')
            draft, tools, window = self.setup_draft(temp)
            selected = {}
            def open_uri(uri):
                self.assertNotIn('+', uri)
                self.assertIn('%20', uri)
                selected['path'] = Path(parse_qs(urlsplit(uri).query)['path'][0])
                window['title'] = selected['path'].stem + ' - vault - Obsidian'
            payload = 'Onyx, не закрывай VS Code. Мне нужен текст, не команда.'
            def enter(text, submit, target_guard):
                self.assertFalse(submit)
                target_guard()
                selected['path'].write_text(text, encoding='utf-8')
                return dict(verified=False, typed=True)
            tools.text_entry.enter.side_effect = enter
            with patch('os.startfile', side_effect=open_uri), patch('kk.tools.windows.native.foreground_window', return_value=window):
                result = draft.create(payload)
            self.assertTrue(result['verified'])
            self.assertTrue(result['file_readback'])
            self.assertEqual(existing.read_text(encoding='utf-8'), 'Keep this note')
            self.assertEqual(selected['path'].read_text(encoding='utf-8'), payload)
            self.assertTrue(result['target']['verified_at'])

    def test_uri_dispatch_without_note_identity_never_types(self):
        with TemporaryDirectory() as temp:
            draft, tools, window = self.setup_draft(temp)
            with patch('os.startfile'), patch('kk.tools.windows.native.foreground_window', return_value=window), \
                 patch('kk.tools.windows.obsidian.monotonic', side_effect=[0, 0, 8]), patch('kk.tools.windows.obsidian.sleep'):
                result = draft.create('Do not place in old note')
            self.assertFalse(result['verified'])
            self.assertFalse(result['typed'])
            tools.text_entry.enter.assert_not_called()
            self.assertTrue(all(p.read_text() == '' for p in Path(temp).glob('*.md')))

    def test_blank_search_field_is_not_note_editor(self):
        self.assertFalse(ObsidianDrafts._editor({'control_type':'EditControl', 'class_name':'search-input', 'value':''}))
        self.assertFalse(ObsidianDrafts._editor({'control_type':'EditControl', 'class_name':'cm-content'}))
        self.assertTrue(ObsidianDrafts._editor({'control_type':'EditControl', 'class_name':'cm-content cm-lineWrapping', 'value':'\n'}))
