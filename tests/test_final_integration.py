"""Final-pass contracts: isolated storage, real IPC/queue/orchestrator, no GUI/audio."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import json
import logging

from kk.core.agent import Agent
from kk.core.brain import ContextBroker
from kk.core.control import RuntimeControl, request_control
from kk.core.decision_policy import DecisionPolicy
from kk.core.orchestrator import Orchestrator
from kk.core.state import StateMachine, AgentState
from kk.core.task import AgentTask
from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionPolicy, PermissionLevel
from kk.providers.base import LLMResponse, ToolCall
from kk.tools.memory import register_memory_tools
from kk.tools.registry import ToolRegistry, Tool
from kk.tools.windows.files import list_directory
from kk.voice.loop import VoiceLoop


class FixtureRuntime:
    def __init__(self, root, provider=None):
        self.root = Path(root)
        self.memory = MemoryDatabase(self.root/'data/kk.db')
        self.state = StateMachine(self.memory)
        self.provider = provider or SimpleNamespace(context_window=4096, chat=Mock(return_value=LLMResponse('Ответ fixture.')))
        self.policy = PermissionPolicy(PermissionLevel.ACTION, self.root/'STOP')
        self.tools = ToolRegistry(self.policy, self.memory, logging.getLogger('final_fixture'))
        self.tools.visual_state = self.state
        self.tools.decision_policy = DecisionPolicy({'browser':'DefaultFixture'},
            path=self.root/'preferences.json', memory=self.memory)
        self.tools.app_resolver = SimpleNamespace(resolve=lambda value: value)
        register_memory_tools(self.tools, self.memory)
        self.tools.register(Tool('observe_context', 'Fixture only', {'type':'object','properties':{}},
                                 PermissionLevel.READ, lambda: {}))
        self.tools.register(Tool('list_directory', 'List names and metadata',
            {'type':'object','properties':{'path':{'type':'string'}},'required':['path']},
            PermissionLevel.READ, list_directory))
        self.orchestrator = Orchestrator(self.provider, 'fixture', self.tools, self.memory,
                                         logging.getLogger('final_fixture'), self.state)
        self.agent = Agent(self.orchestrator)
        self.model = 'fixture'
        self.context = SimpleNamespace(semantic_diagnostics=lambda: {'stage':'offline_fixture'})
        self.loop = VoiceLoop(self.agent, Mock(), Mock(muted=True), self.state, self.policy)
        self.loop.capture = SimpleNamespace(ready=Event())
        self.loop.capture.ready.set()
        self.state.set_readiness(True)
        self.shutdown = Event()
        self.control = RuntimeControl.background(SimpleNamespace(root=self.root), self,
            SimpleNamespace(loop=self.loop), self.shutdown.set)
        self.control.start()

    def ask(self, text):
        return request_control(self.root, 'ask', text=text, spoken=False, diagnostic=True, timeout=10)

    def close(self):
        self.control.close()
        self.loop.stop()
        if self.loop.worker: self.loop.worker.join(3)
        self.loop.speech.close()
        self.memory.close()


class FinalIntegrationTests(TestCase):
    def test_desktop_live_ack_reuses_capture_and_preserves_hidden_aura(self):
        runtime=FixtureRuntime(self.root)
        try:
            loop=runtime.loop;capture=loop.capture
            loop.conversation=SimpleNamespace(active=False)
            loop.conversation.touch=lambda:setattr(loop.conversation,'active',True)
            cid=runtime.control.desktop.create()['conversation_id']
            other=runtime.control.desktop.create()['conversation_id']
            runtime.state.set_visual_visible(False)
            result=request_control(self.root,'voice.live',conversation_id=cid)
            self.assertTrue(result['started']);self.assertEqual(result['conversation_id'],cid)
            self.assertIs(loop.capture,capture);self.assertTrue(loop._trigger.is_set())
            self.assertTrue(runtime.state.visual_snapshot()['user_visual_hidden'])
            self.assertEqual(loop.desktop_live_scope,cid)
            with self.assertRaises(RuntimeError):request_control(self.root,'voice.live',conversation_id=other)
            loop.paused=True
            with self.assertRaises(RuntimeError):request_control(self.root,'voice.live',conversation_id=cid)
        finally:runtime.close()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # COM setup itself is replaced; no live desktop is inspected or driven.
        initializer = patch('uiautomation.UIAutomationInitializerInThread', return_value=nullcontext())
        initializer.start()
        self.addCleanup(initializer.stop)

    def test_files_production_queue_4096_budget_and_long_history(self):
        folder = self.root/'Downloads'; folder.mkdir()
        (folder/'fixture.txt').touch()
        provider = SimpleNamespace(context_window=4096, chat=Mock(side_effect=[
            LLMResponse(tool_calls=[ToolCall('files','list_directory',{'path':str(folder)})]),
            LLMResponse('fixture.txt')]))
        runtime = FixtureRuntime(self.root, provider)
        try:
            runtime.orchestrator.short_term.add('assistant', 'Earlier context '*3000)
            budgets = []
            compact = runtime.orchestrator.context_broker.compact_rounds
            def inspect(*args, **kwargs):
                budgets.append(kwargs['budget'])
                return compact(*args, **kwargs)
            runtime.orchestrator.context_broker.compact_rounds = inspect
            with patch.object(Path, 'read_text', autospec=True, side_effect=Path.read_text) as read:
                result = runtime.ask('Посмотри Загрузки и перечисли только имена файлов без чтения содержимого')
            self.assertEqual(result['status'], 'COMPLETED', result)
            self.assertEqual(result['answer'], 'fixture.txt')
            self.assertEqual([a['tool'] for a in result['actions']], ['list_directory'])
            self.assertTrue(budgets and all(type(b) is int for b in budgets))
            self.assertFalse(any(call.args[0].parent == folder for call in read.call_args_list))
        finally:
            runtime.close()

    def test_budget_accepts_exact_float_but_rejects_lossy_and_wrong_types(self):
        task = AgentTask('Names only')
        messages = [{'role':'system','content':'Contract'}, {'role':'assistant','content':'x'*20000},
                    {'role':'user','content':task.user_request}]
        result = ContextBroker().compact_rounds(messages, task, budget=4096 * 1.5)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 6144)
        for value in (6144.5, True, '6144', None, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ContextBroker().compact_rounds(messages, task, budget=value)

    def test_preference_ipc_restart_correct_forget_and_next_decision(self):
        runtime = FixtureRuntime(self.root)
        try:
            runtime.memory.remember('unrelated.user', 'preserve')
            saved = runtime.ask('Сохрани предпочтение application.browser: FirstFixture')
            self.assertEqual(saved['status'], 'COMPLETED', saved)
            self.assertEqual(runtime.tools.decision_policy.application('браузер'), 'FirstFixture')
            self.assertTrue(request_control(self.root, 'shutdown')['shutdown_requested'])
            self.assertTrue(runtime.shutdown.wait(2))
        finally:
            runtime.close()
        runtime = FixtureRuntime(self.root)
        try:
            self.assertTrue(request_control(self.root, 'status')['voice_ready'])
            self.assertEqual(runtime.tools.decision_policy.application('браузер'), 'FirstFixture')
            self.assertEqual(runtime.ask('Исправь предпочтение application.browser: SecondFixture')['status'], 'COMPLETED')
            self.assertEqual(runtime.tools.decision_policy.application('браузер'), 'SecondFixture')
            self.assertEqual(runtime.memory.recall('FirstFixture'), [])
            runtime.orchestrator.short_term.add('assistant', 'FirstFixture SecondFixture')
            forgotten = runtime.ask('Забудь предпочтение application.browser')
            self.assertEqual(forgotten['status'], 'COMPLETED', forgotten)
            self.assertEqual(runtime.tools.decision_policy.application('браузер'), 'DefaultFixture')
            self.assertEqual(runtime.memory.recall('SecondFixture'), [])
            self.assertEqual(runtime.memory.connection.execute("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'SecondFixture'").fetchone()[0], 0)
            runtime.ask('Какое предпочтение application.browser сейчас известно?')
            sent = json.dumps(runtime.provider.chat.call_args.args, ensure_ascii=False)
            self.assertNotIn('FirstFixture', sent)
            self.assertNotIn('SecondFixture', sent)
            self.assertEqual(runtime.memory.get('unrelated.user')['value'], 'preserve')
            broad = runtime.ask('Забудь всё обо мне')
            self.assertIn('Широкий запрос ничего не удалил', broad['answer'])
            self.assertEqual(runtime.memory.get('unrelated.user')['value'], 'preserve')
        finally:
            runtime.close()

    def test_edge_visibility_persists_without_tts_or_readiness_overriding(self):
        path = self.root/'memory.db'
        db = MemoryDatabase(path)
        state = StateMachine(db)
        state.set_visual_visible(False)
        db.close()
        db = MemoryDatabase(path)
        try:
            state = StateMachine(db)
            for value in (AgentState.SPEAKING, AgentState.THINKING, AgentState.IDLE):
                state.set(value)
                state.set_readiness(True)
                state.activate_visual('test')
                self.assertTrue(state.user_visual_hidden)
            state.set_visual_visible(True)
            self.assertFalse(StateMachine(db).user_visual_hidden)
        finally:
            db.close()

    def test_forget_visibility_drops_active_and_persisted_preference(self):
        runtime = FixtureRuntime(self.root)
        try:
            runtime.state.set_visual_visible(False)
            result = runtime.ask('Забудь предпочтение ui.visual_visible')
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertFalse(runtime.state.user_visual_hidden)
            self.assertIsNone(runtime.memory.get('ui.visual_visible'))
        finally:
            runtime.close()

    def test_pause_stops_frame_and_episode_production_and_resume_does_not_replay(self):
        from kk.ambient.manager import AmbientContextManager
        manager = AmbientContextManager(self.root/'ambient.db')
        manager.frames['fixture'] = object()
        try:
            manager.pause(True)
            before = manager.observation_counters()
            with patch('kk.ambient.manager.ImageGrab.grab') as capture:
                manager.sample(screen=True)
                manager._screen_sample([{'monitor_id':'fixture'}])
                manager._event('USER_UI_INVOKED', details={'name':'Not observed'})
                capture.assert_not_called()
            self.assertEqual(before, manager.observation_counters())
            self.assertEqual(before['frames'], 0)
            manager.pause(False)
            self.assertEqual(manager.timeline.persisted(), [])
            self.assertEqual(manager.recent()['summary']['user_ui_actions'], [])
        finally:
            manager.stop()

    def test_summary_does_not_attribute_paused_interval_to_foreground_work(self):
        from kk.ambient.model import compress_context
        events = [dict(timestamp=10, ended=20, event='WINDOW_FOCUSED',
                       window={'process':'Fixture.exe','title':'Fixture','monitor_id':'one'}),
                  dict(timestamp=200, event='WINDOW_FOCUSED',
                       window={'process':'Fixture.exe','title':'Fixture','monitor_id':'one'})]
        self.assertEqual(compress_context(events)['activities'][0]['seconds'], 10)

    def test_semantic_fixture_answer_is_sourced_and_unknown_target_is_not_invented(self):
        from kk.ambient.manager import AmbientContextManager
        from time import time, monotonic
        runtime = FixtureRuntime(self.root)
        manager = AmbientContextManager(self.root/'ambient.db')
        window = {'hwnd':42, 'process_id':71, 'process':'Fixture.exe','title':'Onyx Fixture'}
        runtime.tools.ambient = manager
        manager.foreground = dict(window)
        try:
            with patch('kk.ambient.manager.idle_seconds', return_value=.1), patch('kk.ui.capture_guard.is_own_window', return_value=False):
                manager.enqueue_ui_event(dict(window=window, process_id=71, operation='invoke',
                    control_type='ButtonControl', name='Поиск', automation_id='fixture_search',
                    uia_event_id=20009, timestamp=time(), observed_monotonic=monotonic(), source='test_automation'))
                manager._drain_ui_events()
            manager.timeline.events.clear()  # force persisted retrieval
            answer = runtime.ask('Что я нажимал в Onyx Fixture?')['answer']
            self.assertIn('Поиск', answer)
            self.assertIn('ambient:activity:', answer)
            self.assertIn('тестовая автоматизация', answer)
            answer = runtime.ask('Что я нажимал в Another Fixture?')['answer']
            self.assertNotIn('Поиск', answer)
            self.assertIn('не доказывают', answer)
        finally:
            runtime.close()
            manager.stop()
