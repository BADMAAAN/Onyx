from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from time import monotonic
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from kk.ambient.manager import AmbientContextManager, screen_difference
from kk.ambient.model import ContextTimeline, compress_context, previous_window, safe_title, safe_window
from kk.core.fast_commands import run_fast
from kk.core.task import AgentTask
from kk.core.state import StateMachine
from kk.tools.registry import ToolResult


def window(hwnd, title, monitor='DISPLAY1', process='Code.exe', bounds=None):
    return {'hwnd': hwnd, 'title': title, 'process': process, 'monitor_id': monitor,
            'monitor_index': 1 if monitor == 'DISPLAY1' else 2,
            'bounds': bounds or [0,0,500,500]}


MONITORS = [{'monitor_id':'DISPLAY1','left':0,'top':0,'right':1920,'bottom':1080},
            {'monitor_id':'DISPLAY2','left':1920,'top':0,'right':3840,'bottom':1080}]


class AmbientTests(unittest.TestCase):
    def test_coalescing_ring_and_sqlite(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'activity.db'
            timeline = ContextTimeline(path, retention_seconds=600, max_events=2)
            first = timeline.add('WINDOW_FOCUSED', window(1,'agent.py'), at=100)
            for t in (101, 102):
                timeline.add('WINDOW_FOCUSED', window(1,'agent.py'), at=t)
            self.assertEqual(first['count'], 3)
            self.assertEqual(timeline.writes, 1)
            timeline.add('WINDOW_OPENED', window(2,'Telegram'), at=103)
            timeline.add('WINDOW_FOCUSED', window(2,'Telegram'), at=104)
            self.assertEqual(len(timeline.events), 2)
            self.assertEqual(len(timeline.persisted(1000, now=105)), 3)
            timeline.close()

    def test_privacy_filter(self):
        self.assertEqual(safe_title('Vault - Bitwarden'), '[protected]')
        self.assertEqual(safe_window(window(1,'Your OTP code 123456'))['title'], '[protected]')
        self.assertEqual(safe_window(window(1,'agent.py'))['title'], 'agent.py')

    def test_multi_monitor_transition_and_snapshot(self):
        with TemporaryDirectory() as folder:
            manager = AmbientContextManager(Path(folder)/'ambient.db', uia=None)
            a = window(1,'agent.py')
            telegram = window(2,'AyuGram','DISPLAY2','AyuGram.exe')
            manager.sample(windows=[a, telegram], foreground=a, monitors=MONITORS, idle=0, screen=False)
            self.assertEqual(len(manager.snapshot()['windows_by_monitor']), 2)
            moved = window(1,'agent.py','DISPLAY2',bounds=[2100,0,2600,500])
            manager.sample(windows=[moved, telegram], foreground=telegram, monitors=MONITORS, idle=0, screen=False)
            kinds = [e['event'] for e in manager.timeline.recent()]
            self.assertIn('MONITOR_CHANGED', kinds)
            self.assertEqual(manager.previous('AyuGram')['hwnd'], 1)
            self.assertEqual(manager.snapshot()['foreground_monitor'], 'DISPLAY2')
            manager.stop()

    def test_uia_failure_and_topology_change_are_nonfatal(self):
        with TemporaryDirectory() as folder:
            uia=Mock()
            uia.tree.side_effect=RuntimeError('UIA temporarily unavailable')
            manager=AmbientContextManager(Path(folder)/'ambient.db',uia=uia)
            a=window(1,'agent.py')
            manager.sample(windows=[a],foreground=a,monitors=MONITORS,idle=0)
            self.assertIsNone(manager.focused_ui)
            self.assertEqual(manager.uia_errors,1)
            manager.sample(windows=[a],foreground=a,monitors=MONITORS[:1],idle=0)
            self.assertEqual(len(manager.monitors),1)
            self.assertEqual(manager.snapshot()['foreground_window']['hwnd'],1)
            manager.stop()

    def test_minimize_restore_close_lifecycle(self):
        with TemporaryDirectory() as folder:
            manager=AmbientContextManager(Path(folder)/'ambient.db',uia=None)
            a=window(1,'notes',process='Notepad.exe')
            manager.sample(windows=[a],foreground=a,monitors=MONITORS,idle=0)
            minimized={**a,'minimized':True}
            manager.sample(windows=[minimized],foreground={},monitors=MONITORS,idle=0)
            manager.sample(windows=[a],foreground=a,monitors=MONITORS,idle=0)
            manager.sample(windows=[],foreground={},monitors=MONITORS,idle=0)
            kinds=[e['event'] for e in manager.timeline.recent()]
            self.assertIn('WINDOW_MINIMIZED',kinds)
            self.assertIn('WINDOW_RESTORED',kinds)
            self.assertIn('WINDOW_CLOSED',kinds)
            manager.stop()

    def test_reused_hwnd_is_not_restored(self):
        with TemporaryDirectory() as folder:
            manager=AmbientContextManager(Path(folder)/'ambient.db',uia=None)
            manager.previous=lambda before_app='': {'hwnd':42,'process':'Code.exe',
                'process_id':101,'title':'Old document'}
            reused={'hwnd':42,'process':'Code.exe','process_id':202,'title':'New document'}
            with patch('kk.tools.windows.windows.list_visible_windows',return_value=[reused]):
                with self.assertRaises(LookupError):
                    manager.return_to_previous()
            manager.stop()

    def test_title_change_is_not_claimed_as_edit(self):
        with TemporaryDirectory() as folder:
            manager=AmbientContextManager(Path(folder)/'ambient.db',uia=None)
            a=window(1,'file-a - VS Code')
            b=window(1,'file-b - VS Code')
            manager.sample(windows=[a],foreground=a,monitors=MONITORS,idle=0)
            manager.sample(windows=[b],foreground=b,monitors=MONITORS,idle=0)
            kinds=[e['event'] for e in manager.timeline.recent()]
            self.assertIn('WINDOW_TITLE_CHANGED',kinds)
            self.assertNotIn('DOCUMENT_MODIFIED',kinds)
            manager.stop()

    def test_tray_pause_callback(self):
        from kk.ui.tray import TrayController
        context=Mock()
        context.paused=False
        context.pause.side_effect=lambda paused: setattr(context,'paused',paused)
        tray=TrayController.__new__(TrayController)
        tray.runtime=SimpleNamespace(context=context)
        tray._toggle_ambient()
        self.assertTrue(context.paused)
        tray._toggle_ambient()
        self.assertFalse(context.paused)
        self.assertEqual(context.pause.call_count,2)

    def test_session_and_pause(self):
        with TemporaryDirectory() as folder:
            manager = AmbientContextManager(Path(folder)/'ambient.db', uia=None)
            a = window(1,'agent.py')
            manager.sample(windows=[a], foreground=a, monitors=MONITORS, idle=0, now=1000)
            manager.sample(windows=[a], foreground=a, monitors=MONITORS, idle=250, now=1250)
            manager.sample(windows=[a], foreground=a, monitors=MONITORS, idle=0, now=1900)
            self.assertEqual(manager.timeline.session_id, 1)
            self.assertIn('SESSION_STARTED', [e['event'] for e in manager.timeline.recent()])
            count = len(manager.timeline.events)
            manager.pause(True)
            manager.sample(windows=[], foreground={}, monitors=MONITORS, idle=0)
            self.assertEqual(len(manager.timeline.events), count)
            manager.stop()

    def test_pause_discards_unobserved_changes_and_tool_events(self):
        with TemporaryDirectory() as folder:
            manager = AmbientContextManager(Path(folder)/'ambient.db', uia=None)
            try:
                a, b = window(1,'agent.py'), window(2,'Private notes',process='Notepad.exe')
                manager.sample(windows=[a], foreground=a, monitors=MONITORS, idle=0)
                count = len(manager.timeline.persisted())
                manager.pause(True)  # closes prior focus interval, adds no episode
                self.assertEqual(len(manager.timeline.persisted()), count)
                before = manager.timeline.writes
                manager.sample(windows=[b], foreground=b, monitors=MONITORS, idle=0)
                manager.record_tool('open_application', {'name':'notepad'}, True)
                self.assertEqual(manager.timeline.writes, before)
                manager.pause(False)
                manager.sample(windows=[b], foreground=b, monitors=MONITORS, idle=0)
                kinds = [e['event'] for e in manager.timeline.recent()]
                self.assertNotIn('WINDOW_CLOSED', kinds)
                self.assertNotIn('WINDOW_OPENED', kinds)
                self.assertNotIn('KK_ACTION', kinds)
                self.assertEqual(manager.snapshot()['foreground_window']['hwnd'], 2)
            finally:
                manager.stop()

    def test_previous_and_compression(self):
        a, b = window(1,'agent.py'), window(2,'AyuGram',process='AyuGram.exe')
        events = [{'timestamp':10,'event':'WINDOW_FOCUSED','window':a,'details':{}},
                  {'timestamp':20,'event':'WINDOW_FOCUSED','window':b,'details':{}},
                  {'timestamp':30,'event':'WINDOW_FOCUSED','window':a,'details':{}}]
        self.assertEqual(previous_window(events, a)['hwnd'], 2)
        summary = compress_context(events, a)
        self.assertEqual(summary['previous_activity']['hwnd'], 2)
        self.assertEqual(sum(item['seconds'] for item in summary['activities']), 20)
        with_idle = [events[0], {'timestamp':15,'event':'USER_IDLE','window':a,'details':{}},
                     {'timestamp':100,'event':'SESSION_STARTED','window':a,'details':{}},
                     {'timestamp':101,'event':'WINDOW_FOCUSED','window':a,'details':{}},
                     {'timestamp':111,'event':'USER_ACTIVE','window':a,'details':{}}]
        self.assertEqual(sum(item['seconds'] for item in compress_context(with_idle,a)['activities']), 15)

    def test_compression_keeps_order_and_bounds_focus_sequence(self):
        events = []
        for i in range(200):
            w = window(i%5+1, f'document-{i%5}', 'DISPLAY1' if i%2 else 'DISPLAY2',
                       process=['Code.exe','AyuGram.exe','chrome.exe','Notepad.exe','SystemSettings.exe'][i%5])
            events.append({'timestamp':i*3,'event':'WINDOW_FOCUSED','window':w,'details':{}})
        result = compress_context(events,events[-1]['window'])
        self.assertEqual(len(result['focus_sequence']),12)
        self.assertEqual(result['focus_sequence'][-1]['process'],'SystemSettings.exe')
        self.assertLessEqual(len(result['activities']),8)

    def test_sqlite_retention_preserves_fresh_activity(self):
        with TemporaryDirectory() as folder:
            memory_path=Path(folder)/'kk.db'
            memory=sqlite3.connect(memory_path)
            memory.execute('CREATE TABLE memory (value TEXT)')
            memory.execute('INSERT INTO memory VALUES (?)',('keep',))
            memory.commit(); memory.close()
            manager = AmbientContextManager(Path(folder)/'ambient.db', uia=None)
            old = time()-8*86400
            fresh = time()
            manager.timeline.add('WINDOW_FOCUSED',window(1,'old'),at=old)
            manager.timeline.add('WINDOW_FOCUSED',window(2,'fresh'),at=fresh)
            manager.last_db_prune = old
            manager.sample(windows=[window(2,'fresh')],foreground=window(2,'fresh'),
                           monitors=MONITORS,idle=0,now=fresh)
            titles = [row[0] for row in manager.timeline.db.execute('SELECT title FROM activity')]
            self.assertNotIn('old',titles)
            self.assertIn('fresh',titles)
            manager.stop()
            memory=sqlite3.connect(memory_path)
            self.assertEqual(memory.execute('SELECT value FROM memory').fetchone()[0],'keep')
            memory.close()

    def test_sqlite_lock_keeps_memory_events_and_recovers(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'ambient.db'
            manager = AmbientContextManager(path,uia=None)
            lock = sqlite3.connect(path)
            lock.execute('BEGIN EXCLUSIVE')
            try:
                start = monotonic()
                manager.timeline.add('WINDOW_FOCUSED',window(1,'agent.py'))
                self.assertLess(monotonic()-start,1)
                self.assertEqual(manager.timeline.writes,0)
                self.assertEqual(len(manager.timeline.events),1)
            finally:
                lock.rollback(); lock.close()
            manager.timeline.add('WINDOW_OPENED',window(2,'new.py'))
            self.assertEqual(manager.timeline.writes,1)
            manager.stop()

    def test_retrieval_filters(self):
        with TemporaryDirectory() as folder:
            manager = AmbientContextManager(Path(folder)/'ambient.db', uia=None)
            a, b = window(1,'agent.py'), window(2,'AyuGram','DISPLAY2','AyuGram.exe')
            manager.sample(windows=[a,b],foreground=a,monitors=MONITORS,idle=0)
            manager.sample(windows=[a,b],foreground=b,monitors=MONITORS,idle=0)
            result = manager.recent(60, app='AyuGram')
            self.assertTrue(result['events'])
            self.assertTrue(all('AyuGram' in e['window']['process'] for e in result['events']))
            self.assertIsNone(manager.retrieve_for_query('какая громкость'))
            self.assertGreater(manager.retrieve_for_query('где Telegram?')['total_events'], 0)
            manager.stop()

    def test_screen_difference_thresholds(self):
        before = Image.new('L',(100,100),0)
        same = Image.new('L',(100,100),2)
        changed = Image.new('L',(100,100),255)
        self.assertFalse(screen_difference(before,same)['changed'])
        self.assertTrue(screen_difference(before,changed)['changed'])

    def test_current_activity_precedes_older_persistent_duration(self):
        code = window(1,'KK project')
        telegram = window(2,'AyuGram','DISPLAY2','AyuGram.exe')
        tools = Mock()
        tools.execute.return_value = ToolResult(True, {'summary': {
            'current_activity': code, 'previous_activity': telegram,
            'activities': [{'process':'explorer.exe','title':'Old folder','seconds':500}]}})
        answer = run_fast('что я сейчас делал?',AgentTask('что я сейчас делал?'),tools,StateMachine())
        self.assertIn('Code.exe',answer)
        self.assertIn('AyuGram.exe',answer)
        self.assertNotIn('explorer.exe',answer)


if __name__ == '__main__':
    unittest.main()
