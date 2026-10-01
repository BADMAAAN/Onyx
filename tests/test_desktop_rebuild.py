from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from tests.test_desktop import FakeLoop
from kk.memory.database import MemoryDatabase
from kk.core.desktop_service import DesktopService
from onyx_desktop.client import Client
from time import monotonic, sleep


class ProjectPersistenceTests(TestCase):
    def test_grouping_is_not_a_conversation_and_survives_archive_restart(self):
        with TemporaryDirectory() as root:
            db=MemoryDatabase(Path(root)/'state.db');loop=FakeLoop();service=DesktopService(db,loop)
            draft='draft:'+'a'*32
            project=service.save_project('Research')[0]
            service.move_to_project(draft,project['project_id'])
            self.assertEqual(service.chats(),[])
            self.assertIsNone(project['workspace_path'])
            service.create(conversation_id=draft);service.pin(draft,True);service.archive(draft,True)
            self.assertEqual(service.chats(archived=True)[0]['project_id'],project['project_id'])
            service.save_project('Notes',project['project_id']);service.close()
            service=DesktopService(db,loop)
            self.assertEqual(service.projects()[0]['title'],'Notes')
            self.assertTrue(service.chats(archived=True)[0]['pinned'])
            with self.assertRaises(ValueError):service.move_to_project(draft,'missing')
            service.delete(draft)
            self.assertEqual(db.connection.execute('SELECT count(*) FROM desktop_chat_projects').fetchone()[0],0)
            self.assertEqual(service.projects()[0]['title'],'Notes')
            service.close();db.close()

    def test_project_moves_never_combine_conversation_context_after_restart(self):
        with TemporaryDirectory() as root:
            path=Path(root)/'state.db';db=MemoryDatabase(path);loop=FakeLoop();loop.release.set()
            service=DesktopService(db,loop)
            a=service.create()['conversation_id'];b=service.create()['conversation_id']
            first=service.save_project('First')[0]['project_id']
            second=next(r['project_id'] for r in service.save_project('Second') if r['title']=='Second')
            service.move_to_project(a,first);service.move_to_project(b,second)
            service.submit(a,'context-request-A','Private project-A marker: violet-184')
            service.submit(b,'context-request-B','Private project-B marker: amber-629')
            deadline=monotonic()+3
            while service.tokens and monotonic()<deadline:sleep(.02)
            self.assertFalse(service.tokens)
            service.close();db.close()
            db=MemoryDatabase(path);service=DesktopService(db,FakeLoop())
            try:
                service.move_to_project(b,first)  # Sharing a project is not consent to merge chat context.
                self.assertEqual([r['content'] for r in service.prompt_history(a) if r['role']=='user'],['Private project-A marker: violet-184'])
                self.assertEqual([r['content'] for r in service.prompt_history(b) if r['role']=='user'],['Private project-B marker: amber-629'])
                self.assertEqual(len(service.chats()),2)
            finally:service.close();db.close()


class MemoryCorrectionPersistenceTests(TestCase):
    def test_save_restart_retrieve_correct_restart_and_forget_one_preference(self):
        from kk.tools.memory import _remember, _forget
        from types import SimpleNamespace
        from unittest.mock import Mock
        with TemporaryDirectory() as root:
            path=Path(root)/'memory.db';db=MemoryDatabase(path)
            registry=SimpleNamespace(memory_context_changed=Mock())
            _remember(db,'fixture.preference','Neutral cobalt','preference',registry)
            db.remember('fixture.keep','Unrelated preference')
            original_id=db.get('fixture.preference')['id'];db.close();db=MemoryDatabase(path)
            self.assertEqual(db.recall('cobalt')[0]['value'],'Neutral cobalt')
            _remember(db,'fixture.preference','Corrected emerald','preference',registry)
            self.assertEqual(db.get('fixture.preference')['id'],original_id)
            self.assertEqual(db.recall('cobalt'),[])
            db.close();db=MemoryDatabase(path)
            self.assertEqual(db.recall('emerald')[0]['value'],'Corrected emerald')
            self.assertTrue(_forget(db,registry,'fixture.preference')['forgotten'])
            self.assertEqual(db.recall('emerald'),[])
            self.assertIsNone(db.get('fixture.preference'))
            self.assertEqual(db.get('fixture.keep')['value'],'Unrelated preference')
            self.assertEqual(registry.memory_context_changed.call_count,3)
            db.close()


class SelectionAndUpdateTests(TestCase):
    def test_late_snapshot_cannot_replace_acknowledged_model(self):
        c=Client('.', 'unused',preview=True);cid=c.selected
        c.receive('models',dict(conversation_id=cid,model_id='model-a',selection_revision=1,rows=[]))
        c._modelPending.add(cid)
        c.receive('modelSelected',dict(conversation_id=cid,model_id='model-b',selection_revision=2,rows=[]))
        c.receive('models',dict(conversation_id=cid,model_id='model-a',selection_revision=1,rows=[]))
        self.assertEqual(c.currentModel,'model-b');self.assertFalse(c.modelChanging)
    def test_voice_envelope_does_not_invalidate_all_bindings(self):
        c=Client('.', 'unused',preview=True);calls=[];c.changed.connect(lambda:calls.append(1))
        state=dict(text_ready=True,voice_state='listening',voice_reason='',voice_envelope=0)
        c.receive('hello',state);calls.clear()
        c.receive('hello',dict(state,voice_envelope=.4))
        self.assertEqual(c.voiceEnvelope,.4);self.assertEqual(calls,[])
