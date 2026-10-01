"""Fixture-only final integration regressions: no live input, model or audio."""
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from kk.core.decision_policy import DecisionPolicy
from kk.core.desktop_commands import parse_plan
from kk.memory.database import MemoryDatabase
from kk.memory.long_term import LongTermMemory
from kk.tools.memory import register_memory_tools
from kk.tools.registry import ToolRegistry, ToolResult
from kk.tools.windows.app_resolver import ApplicationResolver, AppEntry, canonical


class ApplicationIntegrationTests(TestCase):
    def test_spoken_code_and_visual_studio_remain_distinct(self):
        for phrase in ('ви эс код', 'вэ эс код', 'VS Code'):
            self.assertEqual(canonical(phrase), 'vscode')
            self.assertEqual(parse_plan('Открой ' + phrase)[0]['app'], 'VS Code')
        self.assertEqual(canonical('Visual Studio Community 2022'), 'visualstudio')
        self.assertEqual(canonical('devenv.exe'), 'visualstudio')
        self.assertEqual(parse_plan('Открой Visual Studio')[0]['app'], 'Visual Studio')
        self.assertIsNone(parse_plan('Расскажи про VS Code'))

    def test_missing_studio_does_not_fuzzy_launch_code(self):
        resolver = ApplicationResolver()
        resolver.entries = [AppEntry('Visual Studio Code','executable','Code.exe','Code.exe')]
        resolver.updated = monotonic()
        resolver.refresh = Mock()
        with self.assertRaises(LookupError): resolver.resolve('Visual Studio')

    def test_explicit_music_application_overrides_preference(self):
        policy = DecisionPolicy({'music_app':'VLC','browser':'Firefox'})
        self.assertEqual(policy.application('Яндекс Музыка'), 'Яндекс Музыка')
        self.assertEqual(policy.application('музыка'), 'VLC')
        self.assertEqual(policy.application('Chrome'), 'Chrome')

    def test_literal_error_is_not_replanned_and_retyped(self):
        from kk.core.literal_intents import run_literal
        from kk.core.task import AgentTask, TaskStatus
        task = AgentTask('Напиши ifconfig')
        tools = SimpleNamespace(_tools={'text_entry':True}, execute=Mock(return_value=ToolResult(False,error='read-back unavailable')))
        answer = run_literal(task.user_request, task, tools)
        self.assertIn('Повторно не вводила', answer)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertNotIn('context_needs_planner', task.metrics)
        tools.execute.assert_called_once_with('text_entry', {'text':'ifconfig','submit':False})


class MemoryIntegrationTests(TestCase):
    def test_preference_restart_correction_forget_changes_resolver_decision(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'memory.db'
            legacy = Path(folder)/'preferences.json'
            legacy.write_text('{"browser":"Firefox"}',encoding='utf-8')
            db = MemoryDatabase(path)
            policy = DecisionPolicy({'browser':'Edge'}, path=legacy, memory=db)
            self.assertEqual(policy.application('браузер'), 'Firefox')
            policy.remember('browser','Chrome')
            db.close()
            db = MemoryDatabase(path)
            try:
                policy = DecisionPolicy({'browser':'Edge'}, path=legacy, memory=db)
                registry = ToolRegistry(Mock(), db, logging.getLogger('fixture'))
                registry.decision_policy = policy
                register_memory_tools(registry, db)
                resolver = ApplicationResolver()
                resolver.entries = [AppEntry(name,'executable',name+'.exe',name+'.exe') for name in ('Chrome','Firefox','Edge')]
                resolver.updated = monotonic()
                resolver.decision_policy = policy
                registry.app_resolver = resolver
                self.assertEqual(resolver.resolve('браузер')[0].name, 'Chrome')
                registry._tools['remember_preference'].handler('application.browser','Firefox')
                self.assertEqual(resolver.resolve('браузер')[0].name, 'Firefox')
                result = registry._tools['forget_memory'].handler('application.browser')
                self.assertTrue(result['forgotten'])
                self.assertEqual(resolver.resolve('браузер')[0].name, 'Edge')
                self.assertEqual(db.recall('Firefox'), [])
                reloaded = DecisionPolicy({'browser':'Edge'}, path=legacy, memory=db)
                self.assertEqual(reloaded.application('браузер'), 'Edge')
            finally: db.close()

    def test_memory_context_has_origin_and_time(self):
        with TemporaryDirectory() as folder:
            db = MemoryDatabase(Path(folder)/'memory.db')
            try:
                db.remember('editor','Code',source='user')
                context = LongTermMemory(db).context('editor')
                self.assertIn('source=user',context)
                self.assertIn('status=user_stated',context)
                self.assertIn('updated=',context)
            finally: db.close()


class SearchIntegrationTests(TestCase):
    def test_existing_firefox_session_opens_url_without_new_browser_process(self):
        from kk.tools.windows.browser import open_url
        target = 'https://example.com/search?q=hello'
        window = {'hwnd':7,'executable':'C:/Firefox/firefox.exe','process':'firefox.exe','process_id':91}
        address = {'control_type':'EditControl','automation_id':'urlbar','value':target}
        resolver = SimpleNamespace(resolve=Mock(return_value=[AppEntry('Firefox','executable',window['executable'],window['executable'])]))
        uia = SimpleNamespace(focused=Mock(return_value=(Mock(),address)),tree=Mock(return_value=[address]))
        with patch('kk.tools.windows.windows.list_visible_windows',return_value=[window]), patch('kk.tools.windows.windows.focus_window_handle'), patch('kk.tools.windows.native.foreground_window',return_value=window), patch('kk.tools.windows.keyboard.press_hotkey') as keys, patch('kk.tools.windows.keyboard.type_text') as typed, patch('kk.tools.windows.browser.subprocess.Popen') as launch, patch('kk.tools.windows.browser.sleep'):
            result = open_url(target,resolver,'Firefox',uia=uia)
        self.assertTrue(result['verified'])
        self.assertEqual(result['hwnd'],7)
        self.assertEqual(typed.call_args.args[0],target)
        launch.assert_not_called()
        self.assertEqual(keys.call_args_list[-1].args[0],['enter'])

    def test_resume_existing_movie_never_navigates_or_chooses_new_video(self):
        from kk.tools.windows.browser import BrowserController
        window = {'hwnd':7,'process':'firefox.exe','title':'The Fixture Movie - Local Player'}
        media = Mock()
        media.call.side_effect = [[{'session_id':'Firefox','title':'The Fixture Movie','playing':False}],
                                  {'session_id':'Firefox','playing':True,'verified':True}]
        browser = BrowserController(SimpleNamespace(check_cancel=Mock()),Mock(),media)
        browser.evidence = Mock(return_value=(window,[],['https://example.com/watch/fixture']))
        browser.current = Mock(return_value=window)
        with patch('kk.tools.windows.browser.open_url') as navigate, patch('kk.tools.windows.keyboard.press_hotkey') as keys:
            result = browser.control('resume_video')
        self.assertTrue(result['verified'])
        media.call.assert_called_with('resume','Firefox')
        navigate.assert_not_called()
        keys.assert_not_called()

    def make_search(self, payload, labels):
        from kk.tools.windows.search import AppSearch
        item = {'name':'Поиск','control_type':'EditControl','value':payload}
        control = Mock()
        launcher = Mock()
        launcher.open.return_value = {'hwnd':7,'process_id':91}
        uia = SimpleNamespace(tree=Mock(return_value=[{'control_type':'TextControl','name':label} for label in labels]))
        search = AppSearch(launcher,uia,Mock())
        search.guard = Mock()
        search.uia_focus = Mock(return_value=(control,item))
        search.focused_edit = Mock(return_value=(control,item))
        return search,control

    def test_two_arbitrary_exact_queries_and_search_result_evidence(self):
        for text in ('OG Будо', 'Sølv 42 — Небо?'):
            with self.subTest(text=text):
                search, control = self.make_search(text, ['Результаты поиска: '+text])
                with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey') as keys, patch('kk.tools.windows.search.sleep'):
                    result = search.run('Яндекс Музыка',text,submit=True)
                self.assertTrue(result['input_verified'])
                self.assertTrue(result['search_verified'])
                self.assertEqual(result['text'],text)
                control.GetValuePattern().SetValue.assert_called_once_with(text)
                keys.assert_called_once_with(['enter'])

    def test_enter_alone_is_not_search_success(self):
        search, _ = self.make_search('Example', ['Поиск'])
        with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey'), patch('kk.tools.windows.search.sleep'):
            result = search.run('Яндекс Музыка','Example',submit=True)
        self.assertTrue(result['input_verified'])
        self.assertTrue(result['submitted'])
        self.assertFalse(result['verified'])

    def test_title_substring_is_not_exact_yandex_track(self):
        from kk.tools.windows.yandex_music import YandexMusicActions
        media = SimpleNamespace(call=Mock(return_value=[{'session_id':'YandexMusic','title':'Song Remix','playing':True}]))
        action = YandexMusicActions(SimpleNamespace(check_cancel=Mock()),Mock(),media)
        with patch('kk.tools.windows.yandex_music.monotonic',side_effect=[0,1,6]), patch('kk.tools.windows.yandex_music.sleep'):
            self.assertFalse(action._verify_playback('Song')['verified'])

    def test_other_application_audio_does_not_prove_browser_playback(self):
        from kk.tools.windows.browser import BrowserController
        window = {'hwnd':5,'process':'firefox.exe','title':'Same Song - YouTube'}
        uia = SimpleNamespace(tree=Mock(return_value=[{'control_type':'EditControl','automation_id':'urlbar','value':'https://youtube.com/watch?v=abc'}]))
        media = SimpleNamespace(call=Mock(return_value=[{'session_id':'YandexMusic','title':'Same Song','playing':True}]))
        browser = BrowserController(SimpleNamespace(check_cancel=Mock()),uia,media)
        browser.current = Mock(return_value=window)
        with patch('kk.tools.windows.native.window_metadata',return_value=window), patch('kk.tools.windows.browser.monotonic',side_effect=[0,1,6]), patch('kk.tools.windows.browser.sleep'):
            self.assertFalse(browser.playback(window)['verified'])


class AmbientIntegrationTests(TestCase):
    def test_own_ui_and_excluded_apps_do_not_enter_memory_or_capture(self):
        from kk.ambient.manager import AmbientContextManager
        monitors = [{'monitor_id':'one','left':0,'top':0,'right':100,'bottom':100}]
        own = {'hwnd':1,'title':'Onyx Edge Presence','process':'pythonw.exe','monitor_id':'one'}
        secret = {'hwnd':2,'title':'Vault contents','process':'vault.exe','monitor_id':'one'}
        with TemporaryDirectory() as folder:
            manager = AmbientContextManager(Path(folder)/'activity.db',excluded_apps=['vault.exe'])
            try:
                with patch('kk.ambient.manager.cursor_position',return_value=(0,0)), patch('kk.ambient.manager.ImageGrab.grab') as capture:
                    manager.sample(windows=[own,secret],foreground=own,monitors=monitors,idle=0,screen=True)
                    self.assertEqual(manager.windows,{})
                    self.assertFalse(manager.foreground.get('hwnd'))
                    capture.assert_not_called()
                    count = manager.timeline.writes
                    manager.pause(True)
                    manager._event('WINDOW_OPENED',secret)
                    manager._screen_sample(monitors)
                    self.assertEqual(manager.timeline.writes,count)
                    capture.assert_not_called()
                self.assertFalse(any('Vault' in str(e) or 'Onyx Edge' in str(e) for e in manager.timeline.recent()))
            finally: manager.stop()


class ContextReadIntegrationTests(TestCase):
    def test_generic_screen_queries_use_fresh_vision_in_requested_scope(self):
        from kk.core.context_routes import route, requests_context_read
        from kk.core.task import AgentTask
        from kk.core.state import StateMachine
        for query, tool, args in [
            ('посмотри что на экране','see_screen',{'target':'active_monitor'}),
            ('что ты видишь на экране','see_screen',{'target':'active_monitor'}),
            ('опиши второй монитор','see_monitor',{'monitor_index':2}),
            ('посмотри на монитор 1. на нём карточка','see_monitor',{'monitor_index':1}),
            ('посмотри на обоих экранах','see_screen',{'target':'all'})]:
            with self.subTest(query=query):
                self.assertTrue(requests_context_read(query))
                tools = SimpleNamespace(_tools={},execute=Mock(return_value=ToolResult(True,{'description':'Observed fixture pixels'})))
                result = route(query,AgentTask(query),tools,StateMachine())
                self.assertEqual(result,'Observed fixture pixels')
                tools.execute.assert_called_once_with(tool,dict(args,question=query,mode='vision'))

    def test_downloads_listing_does_not_read_file_contents(self):
        from kk.core.context_routes import route, requests_context_read
        from kk.core.task import AgentTask
        from kk.core.state import StateMachine
        from kk.tools.windows.files import list_downloads
        with TemporaryDirectory() as folder:
            path = Path(folder)
            (path/'fixture.txt').write_text('Not authorized to read fixture body',encoding='utf-8')
            with patch('kk.tools.windows.files.downloads_folder',return_value=path), patch.object(Path,'read_text') as read:
                data = list_downloads()
                read.assert_not_called()
            self.assertFalse(data['contents_read'])
            self.assertIn('not download time',data['date_meaning'])
            tools = SimpleNamespace(_tools={'list_downloads':True},execute=Mock(return_value=ToolResult(True,data)))
            query = 'посмотри Загрузки'
            self.assertTrue(requests_context_read(query))
            answer = route(query,AgentTask(query),tools,StateMachine())
            self.assertIn('fixture.txt',answer)
            tools.execute.assert_called_once_with('list_downloads',{})

    def test_resume_movie_binds_existing_browser_from_observed_media(self):
        from kk.core.context_routes import route
        from kk.core.task import AgentTask
        from kk.core.state import StateMachine
        window = {'hwnd':7,'process':'firefox.exe','title':'Fixture Movie - Firefox'}
        def execute(name,args):
            return ToolResult(True, {'list_windows':[window],
                'get_media_sessions':[{'session_id':'Firefox','title':'Fixture Movie','playing':False}],
                'focus_window':{'hwnd':7,'verified':True},'browser_control':{'verified':True}}[name])
        tools = SimpleNamespace(_tools={},execute=Mock(side_effect=execute))
        self.assertEqual(route('продолжи фильм',AgentTask('продолжи фильм'),tools,StateMachine()),'Продолжила воспроизведение.')
        self.assertEqual([(c.args[0],c.args[1]) for c in tools.execute.call_args_list][-2:],
                         [('focus_window',{'hwnd':7}),('browser_control',{'action':'resume_video'})])

    def test_ambient_diff_region_expires_and_pause_invalidates(self):
        from kk.ambient.manager import AmbientContextManager
        with TemporaryDirectory() as folder:
            manager = AmbientContextManager(Path(folder)/'activity.db')
            try:
                manager.diff_region = [0,0,100,100]
                manager.diff_observed_at = monotonic() - 6
                self.assertIsNone(manager.recent_diff_region(5))
                manager.diff_observed_at = monotonic()
                self.assertEqual(manager.recent_diff_region(5),[0,0,100,100])
                manager.pause(True)
                self.assertIsNone(manager.recent_diff_region(5))
            finally: manager.stop()

    def test_private_window_does_not_sample_accessible_content(self):
        from kk.ambient.manager import AmbientContextManager
        from kk.ambient.model import safe_title
        with TemporaryDirectory() as folder:
            uia = Mock()
            manager = AmbientContextManager(Path(folder)/'activity.db',uia=uia)
            try:
                manager.foreground = {'title':safe_title('Mozilla Firefox — Private Browsing')}
                manager._uia_sample()
                uia.tree.assert_not_called()
            finally: manager.stop()
