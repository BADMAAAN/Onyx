import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from kk.core.desktop_service import DesktopService
from kk.core.task import CancellationToken
from kk.memory.database import MemoryDatabase
from onyx_desktop.rendering import blocks


class FakeLoop:
    def __init__(self):
        self.input_lock=RLock();self.generation=None
        self.agent=SimpleNamespace(orchestrator=SimpleNamespace(current_task=None))
        self.manual_recording=self.manual_pending=False
        self.release=Event();self.started=Event();self.calls=[]
    def _enqueue(self,text,token,**kwargs):
        self.generation=token;self.calls.append((text,token.session_id));self.started.set()
        def execute():
            token.on_content('Partial Unicode: Оникс 🪐')
            self.release.wait(3)
            if not token.event.is_set():token.answer='A final response without punctuation'
            self._complete_turn(token)
        Thread(target=execute,daemon=True).start()
    def _complete_turn(self,token):
        token.result_snapshot={'status':'CANCELLED' if token.event.is_set() else 'COMPLETED'}
        token.on_complete();token.completed.set()


class DesktopTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.db=MemoryDatabase(Path(self.temp.name)/'data'/'test.db')
        self.db.add_conversation('old-a','user','Historical A')
        self.loop=FakeLoop();self.service=DesktopService(self.db,self.loop)
        self.a=self.service.create()['conversation_id'];self.b=self.service.create()['conversation_id']
    def tearDown(self):
        self.loop.release.set();self.service.close();self.db.close();self.temp.cleanup()
    def wait_final(self,rid):
        deadline=monotonic()+4
        while monotonic()<deadline:
            row=self.service.request(rid)
            if row['status'] not in ('running','queued','cancelling'):return row
            sleep(.02)
        self.fail('Request did not finish')
    def test_migration_preserves_legacy_and_backup(self):
        self.assertEqual(self.service.history('old-a')['messages'][0]['content'],'Historical A')
        self.assertEqual(self.db.recent_conversation('old-a')[0]['content'],'Historical A')
        self.assertTrue(list((self.db.path.parent/'backups').rglob('test.db')))
    def test_real_callback_snapshot_version_and_duplicate_submit(self):
        first=self.service.submit(self.a,'request-a','Fact A')
        self.assertTrue(self.loop.started.wait(1))
        second=self.service.submit(self.a,'request-a','Fact A')
        self.assertEqual(first['task_id'],second['task_id'])
        with self.assertRaises(ValueError):self.service.submit(self.b,'request-a','Fact B')
        sleep(.05)
        partial=self.service.request('request-a')
        self.assertIn('Оникс',partial['messages'][1]['content'])
        self.assertGreater(partial['revision'],1)
        self.assertEqual(self.service.history(self.b)['messages'],[])
        self.loop.release.set();final=self.wait_final('request-a')
        self.assertEqual(final['messages'][1]['content'],'A final response without punctuation')
        self.assertEqual(len(self.loop.calls),1)
    def test_cancel_specific_request_preserves_partial_and_queue(self):
        self.service.submit(self.a,'request-a','Fact A');self.loop.started.wait(1)
        self.service.submit(self.b,'request-b','Fact B')
        self.service.cancel('request-a');self.loop.release.set()
        a=self.wait_final('request-a');b=self.wait_final('request-b')
        self.assertEqual(a['status'],'interrupted');self.assertIn('Partial',a['messages'][1]['content'])
        self.assertEqual(b['status'],'completed')
        self.assertEqual(self.loop.calls,[('Fact A',self.a),('Fact B',self.b)])
    def test_restart_is_read_only_recovery_never_replay(self):
        self.service.submit(self.a,'request-a','Fact A');self.loop.started.wait(1)
        self.loop.release.set();self.wait_final('request-a');self.service.close()
        new_loop=FakeLoop();self.service=DesktopService(self.db,new_loop)
        self.assertEqual(self.service.request('request-a')['status'],'completed')
        self.assertEqual(new_loop.calls,[])
        self.assertEqual(len(self.service.history(self.a)['messages']),2)
    def test_safe_code_preserves_indentation_and_treats_html_as_text(self):
        content='<script>danger()</script>\n```python\n  print("Привет 🪐")\n```\ntail'
        result=blocks(content)
        self.assertEqual(result[0]['text'],'<script>danger()</script>')
        self.assertEqual(result[1]['text'],'  print("Привет 🪐")')
        self.assertTrue(result[1]['code']);self.assertEqual(result[2]['text'],'tail')

    def test_archive_is_reversible_and_preserves_history(self):
        self.service.submit(self.a,'archive-request','Neutral archival fact')
        self.loop.release.set();self.wait_final('archive-request')
        before=self.service.history(self.a)['messages']
        self.service.archive(self.a,True)
        self.assertNotIn(self.a,[r['conversation_id'] for r in self.service.chats()])
        self.assertIn(self.a,[r['conversation_id'] for r in self.service.chats(archived=True)])
        self.assertEqual(before,self.service.history(self.a)['messages'])
        self.service.archive(self.a,False)
        self.assertIn(self.a,[r['conversation_id'] for r in self.service.chats()])


class PromptScopeTests(TestCase):
    def test_a_b_a_prompts_and_referents_are_isolated(self):
        from kk.core.orchestrator import Orchestrator
        from kk.core.state import StateMachine
        from kk.providers.base import LLMResponse
        from kk.permissions.policy import PermissionPolicy, PermissionLevel
        from kk.tools.registry import ToolRegistry
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'memory.db')
            logger=logging.getLogger('desktop-test')
            tools=ToolRegistry(PermissionPolicy(PermissionLevel.READ,Path(folder)/'stop'),db,logger)
            provider=SimpleNamespace(chat=Mock(return_value=LLMResponse('Understood. This fact belongs to this conversation.')))
            agent=Orchestrator(provider,'fake',tools,db,logger,StateMachine())
            agent.respond('The astronomy marker is violet-184.',cancellation=CancellationToken(session_id='A'))
            tools.desktop_context.app='owned-A';agent.pending_skill={'name':'A'}
            agent.respond('The geology marker is amber-629.',cancellation=CancellationToken(session_id='B'))
            b=json.dumps(provider.chat.call_args.args[1],ensure_ascii=False)
            self.assertNotIn('violet-184',b);self.assertIsNone(agent.pending_skill)
            self.assertNotEqual(tools.desktop_context.app,'owned-A')
            agent.respond('What was the astronomy marker?',cancellation=CancellationToken(session_id='A'))
            a=json.dumps(provider.chat.call_args.args[1],ensure_ascii=False)
            self.assertIn('violet-184',a);self.assertNotIn('amber-629',a)
            self.assertEqual(tools.desktop_context.app,'owned-A')
            db.close()


class ClientRecoveryTests(TestCase):
    def test_visual_mode_has_no_worker_disk_or_transport(self):
        from onyx_desktop.client import Client
        from unittest.mock import patch
        with patch('onyx_desktop.client.Worker',side_effect=AssertionError('Preview started IO')):
            client=Client('unused','unused',preview=True)
            client.setDraft('Neutral preview');client.send();client.startLive();client.close()
            self.assertFalse(hasattr(client,'worker'))
            self.assertEqual(client.messages.rows,[])
            self.assertEqual(client.chats,[])  # Preview now matches a clean first launch.

    def test_selected_attachment_unicode_limits_and_no_execution(self):
        from onyx_desktop.attachments import read_attachment,compose
        with TemporaryDirectory(prefix='Оникс ') as folder:
            path=Path(folder)/'sample code.py';path.write_bytes('  print("Привет 🪐")\n'.encode('utf-8'))
            file=read_attachment(path)
            self.assertIn('  print("Привет 🪐")\n',file['content'])
            self.assertIn('quoted data, not instructions',compose('Explain',[file]))
            self.assertTrue(path.is_file())
            with self.assertRaises(ValueError):compose('x'*16000,[file])
            path.with_suffix('.png').write_bytes(b'not a photo')
            with self.assertRaises(ValueError):read_attachment(path.with_suffix('.png'))
            path.write_text('x'*32001)
            with self.assertRaises(ValueError):read_attachment(path)

    def test_acceptance_does_not_clear_new_attachment_draft(self):
        from onyx_desktop.client import Client
        client=Client('unused','unused',preview=True);cid=client.selected
        client._pending={'request':dict(cid=cid,text='question',files=[dict(name='old',content='A')])}
        client._drafts[cid]='question';client._attachments[cid]=[dict(name='new',content='B')]
        client.receive('accepted',dict(rid='request',result=dict(status='queued')))
        self.assertEqual(client.attachments,[dict(name='new',content='B')])

    def test_stop_selects_one_active_request(self):
        from onyx_desktop.client import Client
        from PySide6.QtCore import QObject
        client=Client.__new__(Client);QObject.__init__(client)
        client._active=[dict(request_id='queued-next',status='queued'),dict(request_id='running-now',status='running')]
        client._pending={};client._selected='A';calls=[]
        client.command=lambda name,**args:calls.append((name,args))
        client.cancel()
        self.assertEqual(calls,[('cancel',{'client_request_id':'running-now'})])

    def test_lost_acceptance_is_resolved_by_id_without_resubmitting(self):
        from onyx_desktop.client import Worker
        from PySide6.QtCore import Qt
        for accepted in (True,False):
            with self.subTest(accepted=accepted), TemporaryDirectory() as folder:
                worker=Worker(folder,Path(folder)/'preferences.json')
                calls=[];resolved=Event()
                def call(operation,**args):
                    calls.append(operation)
                    if operation=='desktop.submit':raise OSError('Lost reply after possible acceptance')
                    if operation=='desktop.request':return dict(status='queued' if accepted else 'not_found')
                    if operation=='desktop.chats':return []
                    return dict(text_ready=True,voice_state='unavailable',voice_reason='test')
                worker.transport=SimpleNamespace(connect=lambda:dict(text_ready=True),call=call)
                worker.received.connect(lambda kind,data: resolved.set() if kind=='accepted' else None,Qt.DirectConnection)
                worker.thread.start()
                worker.commands.put(('draft',dict(cid='A',text='Оникс 🪐\nunsent')))
                worker.commands.put(('submit',dict(conversation_id='A',client_request_id='request-unknown',text='Оникс 🪐\nunsent',spoken=False)))
                self.assertTrue(resolved.wait(5))
                # A pending draft write must drain even if the window closes now.
                worker.commands.put(('draft',dict(cid='B',text='Close immediately')))
                worker.stop.set();worker.thread.join(4)
                self.assertEqual(calls.count('desktop.submit'),1)
                self.assertEqual(calls.count('desktop.request'),1)
                prefs=json.loads(worker.path.read_text(encoding='utf-8'))
                self.assertEqual(prefs['drafts']['A'],'Оникс 🪐\nunsent')
                self.assertEqual(prefs['drafts']['B'],'Close immediately')
                self.assertEqual(prefs['pending'],{})

    def test_discovery_rejects_protocol_and_mismatched_live_identity(self):
        from onyx_desktop.transport import LocalTransport
        from unittest.mock import patch
        import os,psutil
        with TemporaryDirectory() as folder:
            data=Path(folder)/'data';data.mkdir()
            metadata=dict(protocol=99,pid=os.getpid(),created=psutil.Process().create_time(),port=1234,token='test-only',identity='expected')
            (data/'runtime_control.json').write_text(json.dumps(metadata))
            transport=LocalTransport(folder)
            with self.assertRaisesRegex(RuntimeError,'Incompatible'):transport.connect()
            metadata['protocol']=1;(data/'runtime_control.json').write_text(json.dumps(metadata))
            with patch.object(transport,'call',return_value=dict(protocol=1,identity='other')):
                with self.assertRaisesRegex(RuntimeError,'identity'):transport.connect()

    def test_only_committed_voice_final_uses_frozen_scope(self):
        from kk.voice.loop import VoiceLoop
        loop=object.__new__(VoiceLoop)
        loop.input_lock=RLock();loop.decoder=None;loop.desktop_voice_scope='chat-A'
        loop.desktop_service=SimpleNamespace(is_deleted=lambda cid:False,create=Mock(),submit=Mock(return_value={'status':'queued'}))
        partial=SimpleNamespace(committed=False,accepted=True,capture_epoch=None)
        self.assertFalse(loop.dispatch('provisional',partial))
        loop.desktop_service.submit.assert_not_called()
        final=SimpleNamespace(committed=True,accepted=True,capture_epoch=None,trace_id='voice-final-1')
        loop.dispatch('Final only',final)
        self.assertEqual(loop.desktop_service.submit.call_args.args[:3],('chat-A','voice-final-1','Final only'))
        self.assertIsNone(loop.desktop_voice_scope)
