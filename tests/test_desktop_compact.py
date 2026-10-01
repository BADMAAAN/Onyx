import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event, RLock
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import Mock
import uuid

from kk.voice.dictation import Dictation, is_stop_live, stop_live
from kk.voice.loop import VoiceLoop
from kk.core.state import StateMachine
from kk.core.desktop_service import DesktopService
from kk.memory.database import MemoryDatabase
from tests.test_desktop import FakeLoop


def recording_loop():
    loop=object.__new__(VoiceLoop)
    ready=Event();ready.set()
    loop.capture=NS(ready=ready,controls=Queue())
    loop.input_lock=RLock();loop.manual_recording=loop.manual_pending=loop.paused=False
    loop.state=StateMachine();loop.decoder=Mock();loop.clarification=NS(pending=None)
    from kk.voice.recognition import RecognitionRecovery
    loop.voice_recovery=RecognitionRecovery();loop.voice_notice=''
    loop.conversation=NS(active=False,touch=Mock());loop.audio_focus=Mock();loop.interrupt_speech=Mock()
    loop.dictation=Dictation(loop)
    return loop


class DictationBoundaryTests(TestCase):
    def setUp(self):
        self.loop=recording_loop();self.d=self.loop.dictation
        self.req=dict(session_id=uuid.uuid4().hex,purpose='dictation',conversation_id='draft:'+'a'*32,revision=4,position=3)
    def test_commands_ru_en_mixed_are_only_text_and_repeated_start_is_idempotent(self):
        for text in ('открой браузер','удали файл','Onyx, turn off Live Assistant','Оникс open browser пожалуйста','отмена'):
            while not self.loop.capture.controls.empty():self.loop.capture.controls.get_nowait()
            self.req['session_id']=uuid.uuid4().hex
            self.d.handle('dictation.start',self.req)
            self.d.handle('dictation.start',self.req)
            self.assertTrue(self.loop.manual_recording)
            self.loop.conversation.touch.assert_not_called()
            self.d.consume('partial',NS(accepted=True,text=text))
            self.assertEqual(self.d.sessions[self.d.active]['state'],'recording')
            self.d.handle('dictation.commit',self.req)
            self.d.consume('final',NS(accepted=True,text=text))
            row=self.d.handle('dictation.status',self.req)
            self.assertEqual(row['text'],text);self.assertEqual(row['state'],'ready')
            self.assertIsNone(self.d.active)
            # This fixture intentionally has no agent / dispatch / LLM at all.
            self.assertFalse(hasattr(self.loop,'agent'))
    def test_cancel_and_stt_error_preserve_identity_and_drop_audio(self):
        self.d.handle('dictation.start',self.req)
        self.d.handle('dictation.cancel',self.req)
        self.assertEqual(self.d.handle('dictation.status',self.req)['state'],'cancelled')
        self.assertFalse(self.d.consume('final',NS(text='late',accepted=True)))
        self.loop.decoder.invalidate.assert_called()
        self.req['session_id']=uuid.uuid4().hex
        while not self.loop.capture.controls.empty():self.loop.capture.controls.get_nowait()
        self.d.handle('dictation.start',self.req);self.d.consume('error')
        self.assertEqual(self.d.handle('dictation.status',self.req)['text'],'')
        self.assertEqual(self.d.handle('dictation.status',self.req)['state'],'error')
    def test_live_and_paused_microphone_are_exclusive(self):
        self.loop.conversation.active=True
        with self.assertRaises(RuntimeError):self.d.handle('dictation.start',self.req)
        self.loop.conversation.active=False;self.loop.paused=True
        with self.assertRaises(RuntimeError):self.d.handle('dictation.start',self.req)
    def test_orphan_and_timed_out_transcriptions_return_error_not_forever_busy(self):
        self.d.handle('dictation.start',self.req);self.d.handle('dictation.commit',self.req)
        self.loop.manual_recording=self.loop.manual_pending=False
        self.assertEqual(self.d.handle('dictation.status',self.req)['state'],'error')
        while not self.loop.capture.controls.empty():self.loop.capture.controls.get_nowait()
        self.req['session_id']=uuid.uuid4().hex
        self.d.handle('dictation.start',self.req);self.d.handle('dictation.commit',self.req)
        self.d.sessions[self.d.active]['commit_started']-=31
        self.assertEqual(self.d.handle('dictation.status',self.req)['state'],'error')
    def test_stop_live_grammar_rejects_negation_and_quotation(self):
        for s in ('Оникс, выключи Live Assistant','закончи голосовой режим','turn off Live Assistant','выключи лайф-ассистента'):
            self.assertTrue(is_stop_live(s),s)
        for s in ('не выключай Live','не выключи live','скажи «выключи Live Assistant»','"turn off Live Assistant"','напиши выключи Live Assistant'):
            self.assertFalse(is_stop_live(s),s)
    def test_stop_live_does_not_cancel_independent_text_task(self):
        loop=self.loop;loop.desktop_live_scope='live';loop.wakeword=False
        loop.generation=NS(source='text',session_id='other',cancel=Mock())
        stop_live(loop)
        loop.generation.cancel.assert_not_called()
        self.assertFalse(loop.conversation.active)


class ChatLifecycleTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.db=MemoryDatabase(Path(self.tmp.name)/'test.db')
        self.loop=FakeLoop();self.service=DesktopService(self.db,self.loop)
    def tearDown(self):
        self.loop.release.set();self.service.close();self.db.close();self.tmp.cleanup()
    def test_pin_archive_and_rename_survive_service_restart(self):
        a=self.service.create()['conversation_id'];b=self.service.create()['conversation_id']
        self.service.pin(a,True);self.service.rename(a,'Нейтральный чат');self.service.archive(b,True)
        self.service.close();self.service=DesktopService(self.db,self.loop)
        self.assertEqual(self.service.chats()[0]['conversation_id'],a)
        self.assertEqual(self.service.chats()[0]['pinned'],1)
        self.assertEqual(self.service.chats()[0]['title'],'Нейтральный чат')
        self.assertEqual(self.service.chats(archived=True)[0]['conversation_id'],b)
    def test_delete_active_chat_late_sources_callbacks_and_resubmit_cannot_resurrect(self):
        a='draft:'+'b'*32;b=self.service.create()['conversation_id']
        self.db.remember('keep','global preference')
        self.service.submit(a,'delete-request','Neutral text');self.loop.started.wait(1)
        token=self.service.tokens['delete-request'];token.web_status='sources';token.web_sources=[]
        self.service.delete(a)
        self.service.append('delete-request','late');self.service.finish('delete-request',token)
        self.service.save_sources('delete-request',token)
        with self.assertRaises(ValueError):self.service.submit(a,'another-request','late')
        with self.assertRaises(ValueError):self.service.create(conversation_id=a)
        self.assertEqual(self.service.history(a)['messages'],[])
        self.assertEqual(self.db.connection.execute('SELECT count(*) FROM desktop_sources').fetchone()[0],0)
        self.assertEqual(self.service.chats()[0]['conversation_id'],b)
        self.assertEqual(self.db.get('keep')['value'],'global preference')
        self.assertTrue(self.service.handle(dict(operation='desktop.delete',conversation_id=a))['deleted'])
    def test_unknown_delete_is_rejected(self):
        with self.assertRaises(ValueError):self.service.delete('unknown')


class DraftTests(TestCase):
    def setUp(self):
        from onyx_desktop.client import Client
        self.c=Client(Path('.'),Path('unused.json'),preview=True)
        self.c._preview=False;self.c.command=Mock();self.c._capabilities=['dictation']
        self.c._selected='a';self.c.setDraft('prefix suffix')
    def test_edit_during_transcription_appends_and_deduplicates_then_one_send(self):
        c=self.c;c.insertTranscript.connect(lambda pos,text:c.setDraft(c.draft[:pos]+text+c.draft[pos:]))
        c.startDictation(7);sid=c._dictation['session_id'];c.setDraft('new text')
        c.send();self.assertFalse(any(x.args[0]=='submit' for x in c.command.call_args_list))
        c.receive('dictation',dict(session_id=sid,state='ready',text='открой браузер'))
        c.receive('dictation',dict(session_id=sid,state='ready',text='открой браузер'))
        self.assertEqual(c.draft,'new text открой браузер')
        c.send();c.send()
        self.assertEqual(sum(x.args[0]=='submit' for x in c.command.call_args_list),1)
    def test_switching_chat_keeps_result_in_original_draft(self):
        c=self.c;c.startDictation(7);sid=c._dictation['session_id'];c.select('b');c.setDraft('other')
        c.receive('dictation',dict(session_id=sid,state='ready',text='test'))
        self.assertEqual(c.draft,'other');self.assertEqual(c._drafts['a'],'prefix test suffix')
    def test_cancel_and_error_never_replace_existing_draft(self):
        c=self.c;c.startDictation(0);sid=c._dictation['session_id'];c.cancelDictation()
        c.receive('dictation',dict(session_id=sid,state='ready',text='late'))
        self.assertEqual(c.draft,'prefix suffix')
        c.startDictation(0);sid=c._dictation['session_id']
        c.receive('dictation',dict(session_id=sid,state='error',error='fixture'))
        self.assertEqual(c.draft,'prefix suffix')


class CompactSettingsTests(TestCase):
    def test_shortcuts_validate_conflicts_and_restart(self):
        from onyx_desktop.preferences import validate,validate_shortcut_conflict,atomic_save,DEFAULTS
        value=validate('shortcutDictation','Ctrl+Shift+R')
        with self.assertRaises(ValueError):validate_shortcut_conflict('shortcutDictation','Ctrl+N',DEFAULTS)
        with self.assertRaises(ValueError):validate('shortcutDictation','Ctrl+Space')
        with self.assertRaises(ValueError):validate('shortcutDictation','A')
        with TemporaryDirectory() as folder:
            path=Path(folder)/'prefs.json';atomic_save(path,dict(DEFAULTS,shortcutDictation=value))
            self.assertEqual(json.loads(path.read_text())['shortcutDictation'],'Ctrl+Shift+R')

    def test_backend_preferences_validate_and_survive_restart(self):
        from kk.core.desktop_settings import DesktopSettings
        with TemporaryDirectory() as folder:
            settings=DesktopSettings(folder)
            for key,value in [('wake_enabled',False),('observation_paused',True),('retention_days',4),('excluded_apps','neutral.exe')]:
                settings.save(key,value)
            other=DesktopSettings(folder)
            self.assertFalse(other.get('wake_enabled'));self.assertTrue(other.get('observation_paused'))
            self.assertEqual(other.get('retention_days'),4);self.assertEqual(other.get('excluded_apps'),['neutral.exe'])
            for key,value in [('wake_enabled','yes'),('retention_days',0),('excluded_apps','invalid/path')]:
                with self.assertRaises(ValueError):settings.save(key,value)
            self.assertEqual(DesktopSettings(folder).get('retention_days'),4)

    def test_memory_retrieval_restart_then_forget_exact_key(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'test.db';db=MemoryDatabase(path)
            db.remember('fixture.preference','Neutral cobalt');db.close();db=MemoryDatabase(path)
            self.assertEqual(db.recall('cobalt')[0]['value'],'Neutral cobalt')
            self.assertTrue(db.forget('fixture.preference'))
            self.assertEqual(db.recall('cobalt'),[]);db.close()
