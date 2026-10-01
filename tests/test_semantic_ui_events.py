"""Semantic UIA event-to-memory regressions; no real COM subscription/input."""
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from kk.ambient.manager import AmbientContextManager
from kk.vision.uia_events import UIASemanticEvents, cached_semantic_metadata


WINDOW = {'hwnd': 42, 'process_id': 71, 'process': 'YandexMusic.exe',
          'title': 'Музыка', 'monitor_id': 'DISPLAY2', 'monitor_index': 2}


def event(**changes):
    return {'window': WINDOW, 'operation': 'invoke', 'process_id': 71,
            'control_type': 'ButtonControl', 'name': 'Поиск', 'automation_id': 'search',
            'uia_event_id': 20009, 'timestamp': time(), 'observed_monotonic': monotonic(), **changes}


def cached_sender(**changes):
    return SimpleNamespace(CachedName='Поиск', CachedAutomationId='search', CachedProcessId=71,
        CachedControlType=50000, CachedIsPassword=False, CachedIsOffscreen=False, **changes)


class SemanticMemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'ambient.db'
        self.manager = AmbientContextManager(self.path)
        self.manager.foreground = dict(WINDOW)
        self.addCleanup(self.manager.stop)
        p = patch('kk.ambient.manager.idle_seconds', return_value=.1)
        self.input_age = p.start(); self.addCleanup(p.stop)
        p = patch('kk.ui.capture_guard.is_own_window', return_value=False)
        self.owned = p.start(); self.addCleanup(p.stop)

    def test_invoke_event_is_a_persisted_source_grounded_episode_and_recall(self):
        self.manager.enqueue_ui_event(event())
        self.manager._drain_ui_events()
        recalled = self.manager.retrieve_for_query('Что я нажимал в музыке?')
        episode = recalled['summary']['user_ui_actions'][0]
        self.assertEqual(episode['event'], 'USER_UI_INVOKED')
        self.assertEqual(episode['details']['name'], 'Поиск')
        self.assertEqual(episode['details']['source'], 'user_ui')
        self.assertEqual(episode['details']['attribution'], 'inferred')
        self.assertEqual(episode['window']['process'], 'YandexMusic.exe')
        self.assertIn('uia_event', episode['details']['evidence'])
        self.manager.timeline.events.clear()
        self.assertEqual(self.manager.recent(keywords='Поиск')['total_events'], 1)
        self.assertEqual(self.manager.timeline.persisted()[0]['details']['uia_event_id'], 20009)

    def test_selection_is_distinct_and_raw_values_are_never_retained(self):
        self.manager.enqueue_ui_event(event(operation='select', control_type='TabItemControl',
            name='Коллекция', uia_event_id=20012, value='private text', text='document', keys='secret', x=15, y=20))
        self.manager._drain_ui_events()
        saved = self.manager.timeline.persisted()[0]
        self.assertEqual(saved['event'], 'USER_UI_SELECTED')
        self.assertEqual(saved['details']['name'], 'Коллекция')
        self.assertFalse({'value', 'text', 'keys', 'x', 'y'} & saved['details'].keys())

    def test_pause_drops_pending_events_and_stale_subscription_after_resume(self):
        self.manager.enqueue_ui_event(event())
        self.manager.pause(True)
        self.manager.enqueue_ui_event(event())
        self.manager._drain_ui_events()
        self.assertEqual(self.manager.timeline.persisted(), [])
        self.manager.pause(False)
        self.manager.foreground = dict(WINDOW)
        self.manager.enqueue_ui_event(event(observation_epoch=0))
        self.manager._drain_ui_events()
        self.assertEqual(self.manager.timeline.persisted(), [])
        self.manager.enqueue_ui_event(event(observation_epoch=self.manager.ui_event_epoch))
        self.manager._drain_ui_events()
        self.assertEqual(len(self.manager.timeline.persisted()), 1)

    def test_password_private_excluded_own_and_edit_controls_are_not_queued(self):
        for change in ({'protected': True}, {'control_type': 'EditControl'},
                       {'name': 'Пароль 123456'}, {'automation_id': 'access_token'},
                       {'window': {**WINDOW, 'title': 'Private browsing'}}):
            self.manager.enqueue_ui_event(event(**change))
        self.manager.excluded_apps.add('yandexmusic.exe')
        self.manager.enqueue_ui_event(event())
        self.manager.excluded_apps.clear()
        self.owned.return_value = True
        self.manager.enqueue_ui_event(event())
        self.assertEqual(len(self.manager.ui_event_queue), 0)

    def test_foreground_process_reuse_and_privacy_change_before_drain_are_rechecked(self):
        self.manager.enqueue_ui_event(event())
        self.manager.foreground['process_id'] = 72
        self.manager._drain_ui_events()
        self.manager.foreground = dict(WINDOW)
        self.manager.enqueue_ui_event(event())
        self.manager.foreground['title'] = '[protected]'
        self.manager._drain_ui_events()
        self.assertEqual(self.manager.timeline.persisted(), [])

    def test_no_recent_input_or_expired_events_do_not_become_user_actions(self):
        self.input_age.return_value = 20
        self.manager.enqueue_ui_event(event())
        self.input_age.return_value = .1
        self.manager.enqueue_ui_event(event(observed_monotonic=monotonic()-10))
        self.manager._drain_ui_events()
        self.assertEqual(self.manager.timeline.persisted(), [])

    def test_tool_scope_suppresses_own_events_and_unwinds_nested_failure(self):
        with self.assertRaises(RuntimeError):
            with self.manager.tool_scope('click_ui_element'):
                with self.manager.tool_scope('nested'):
                    self.manager.enqueue_ui_event(event())
                    raise RuntimeError('tool failed')
        self.assertEqual(self.manager._tool_depth, 0)
        self.manager.enqueue_ui_event(event())  # late provider event in cooldown
        self.assertEqual(len(self.manager.ui_event_queue), 0)
        self.manager._tool_quiet_until = 0
        self.manager.enqueue_ui_event(event())
        self.manager.record_tool('click_ui_element', {}, True)
        self.manager._drain_ui_events()
        self.assertFalse(any(e['event'].startswith('USER_UI_') for e in self.manager.timeline.persisted()))

    def test_queue_and_per_drain_work_are_bounded(self):
        for index in range(100):
            self.manager.enqueue_ui_event(event(name=f'item {index}'))
        self.assertEqual(len(self.manager.ui_event_queue), 64)
        self.manager._drain_ui_events(limit=500)
        self.assertEqual(len(self.manager.ui_event_queue), 56)
        self.assertEqual(self.manager.ui_events_recorded, 8)
        for _ in range(10):
            self.manager._drain_ui_events()
        self.assertEqual(self.manager.ui_events_recorded, 30)

    def test_production_registry_enters_tool_scope_before_handler_on_error(self):
        from kk.memory.database import MemoryDatabase
        from kk.permissions.policy import PermissionPolicy, PermissionLevel
        from kk.tools.registry import ToolRegistry, Tool
        memory = MemoryDatabase(Path(self.folder.name)/'memory.db')
        self.addCleanup(memory.close)
        registry = ToolRegistry(PermissionPolicy(PermissionLevel.ACTION, Path(self.folder.name)/'STOP'), memory, logging.getLogger('test'))
        registry.ambient = self.manager
        def handler():
            self.assertEqual(self.manager._tool_depth, 1)
            self.manager.enqueue_ui_event(event())
            raise RuntimeError('fixture failure')
        registry.register(Tool('fixture_action', 'fixture', {'type': 'object', 'properties': {}}, PermissionLevel.ACTION, handler))
        with self.assertLogs('test', level='ERROR'):
            result = registry.execute('fixture_action')
        self.assertFalse(result.ok)
        self.assertEqual(self.manager._tool_depth, 0)
        self.assertEqual(len(self.manager.ui_event_queue), 0)


class UIAEventContractTests(unittest.TestCase):
    def test_cached_metadata_never_reads_current_properties(self):
        class CachedOnly:
            def __getattr__(self, name):
                if name.startswith('Current'):
                    raise AssertionError('No cross-process current read allowed')
                return getattr(cached_sender(), name)
        result = cached_semantic_metadata(CachedOnly(), 20009)
        self.assertEqual(result['name'], 'Поиск')
        self.assertEqual(result['operation'], 'invoke')
        self.assertIsNone(cached_semantic_metadata(CachedOnly(), 20015))
        sender = cached_sender(); sender.CachedIsPassword = True
        self.assertIsNone(cached_semantic_metadata(sender, 20009))
        sender = cached_sender(); sender.CachedControlType = 50004
        self.assertIsNone(cached_semantic_metadata(sender, 20009))

    def test_foreground_registration_uses_cache_timeouts_and_scoped_removal(self):
        core = SimpleNamespace(CUIAutomation8=object(), IUIAutomation2=object(),
            IUIAutomationEventHandler=object(), TreeScope_Element=1, TreeScope_Subtree=7,
            AutomationElementMode_None=0, **{key: index for index, key in enumerate((
                'UIA_NamePropertyId', 'UIA_ControlTypePropertyId', 'UIA_ProcessIdPropertyId',
                'UIA_AutomationIdPropertyId', 'UIA_IsPasswordPropertyId', 'UIA_IsOffscreenPropertyId'))})
        client = Mock()
        client.ElementFromHandle.return_value = SimpleNamespace(CurrentProcessId=71)
        callback = Mock()
        with patch('comtypes.COMObject', object), patch('comtypes.client.GetModule', return_value=core), \
                patch('comtypes.client.CreateObject', return_value=client):
            listener = UIASemanticEvents(callback)
            listener.watch(WINDOW)
            self.assertEqual(client.ConnectionTimeout, 500)
            self.assertEqual(client.TransactionTimeout, 500)
            self.assertEqual(client.AddAutomationEventHandler.call_count, 3)
            handler = listener.handler
            handler.HandleAutomationEvent(cached_sender(), 20009)
            self.assertEqual(callback.call_args.args[0]['window']['hwnd'], 42)
            listener.close()
            self.assertEqual(client.RemoveAutomationEventHandler.call_count, 3)
            client.RemoveAllEventHandlers.assert_not_called()
            handler.HandleAutomationEvent(cached_sender(), 20009)
            self.assertEqual(callback.call_count, 1)
            client.reset_mock()
            client.AddAutomationEventHandler.side_effect = [None, RuntimeError('provider disappeared')]
            with self.assertRaises(RuntimeError):
                listener.watch(WINDOW)
            self.assertEqual(listener.registered, [])
            self.assertIsNone(listener.identity)
            self.assertEqual(client.RemoveAutomationEventHandler.call_count, 1)


if __name__ == '__main__':
    unittest.main()
