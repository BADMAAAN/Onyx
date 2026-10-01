import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np
from kk.voice.streaming import TurnManager, ConversationManager, StreamingSTT
from kk.voice.audio_focus import AudioFocusTransaction
from kk.voice.stt_faster_whisper import FasterWhisperSTT, Transcription
from kk.tools.windows import filesystem as fs
from kk.tools.windows.development import run_terminal
from kk.core.task import CancellationToken
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.tools.registry import ToolRegistry, Tool
from kk.memory.database import MemoryDatabase

class TurnTests(unittest.TestCase):
    def feed(self, turn, n, speech, now=0):
        events=[]
        for i in range(n): events += turn.feed(bytes(960), speech, now+i*.03)
        return events

    def test_pause_correction_yields_one_final(self):
        turn=TurnManager(silence=.6,incomplete_silence=1.8)
        self.feed(turn,10,True)
        turn.hypothesis(turn.sequence,'Открой')
        self.assertFalse(any(e[0]=='final_audio' for e in self.feed(turn,25,False,.3)))
        self.feed(turn,10,True,1.05)
        turn.hypothesis(turn.sequence,'Открой Chrome нет Firefox')
        events=self.feed(turn,25,False,1.35)
        self.assertEqual(sum(e[0]=='final_audio' for e in events),1)

    def test_silence_and_clicks_do_not_start_turn(self):
        turn=TurnManager()
        events=self.feed(turn,4000,False)
        for i in range(50):
            events+=self.feed(turn,1,True,i)
            events+=self.feed(turn,3,False,i+.03)
        self.assertFalse(events)

    def test_maximum_discards_instead_of_executing_fragment(self):
        turn=TurnManager(maximum=.3)
        events=self.feed(turn,30,True)
        self.assertTrue(any(e[0]=='discard' for e in events))
        self.assertFalse(any(e[0]=='final_audio' for e in events))

    def test_conversation_does_not_expire_during_answer(self):
        c=ConversationManager(5); c.touch(0)
        self.assertFalse(c.expire(True,8))
        self.assertFalse(c.expire(False,12))
        self.assertTrue(c.expire(False,14))

    def test_decoder_discards_obsolete_inflight_result(self):
        started,release,finished=Event(),Event(),Event()
        emitted=[]
        def transcribe(audio):
            started.set(); release.wait(2); finished.set()
            return SimpleNamespace(text='stale')
        decoder=StreamingSTT(SimpleNamespace(transcribe=transcribe),lambda *v:emitted.append(v))
        try:
            decoder.submit('final_audio',1,bytes(960)); self.assertTrue(started.wait(1))
            decoder.invalidate(); release.set(); self.assertTrue(finished.wait(1))
        finally: decoder.close()
        self.assertEqual(emitted,[])

class ConfidenceTests(unittest.TestCase):
    def test_raw_uncertain_entity_preserved_and_rejected(self):
        stt=FasterWhisperSTT(language='auto')
        word=SimpleNamespace(word=' Codex',start=0,end=1,probability=.1)
        segment=SimpleNamespace(text='Открой Codex',start=0,end=1,words=[word],
            avg_logprob=-.2,no_speech_prob=.01,compression_ratio=1)
        stt._model=Mock()
        stt._model.transcribe.return_value=([segment],SimpleNamespace(language='ru',language_probability=.8,duration=1))
        result=stt.transcribe(np.zeros(16000,dtype=np.float32))
        self.assertFalse(result.accepted)
        self.assertEqual(result.raw_transcript,'Открой Codex')
        self.assertEqual(result.normalized_transcript,result.raw_transcript)
        self.assertIsNone(stt._model.transcribe.call_args.kwargs['language'])
        self.assertFalse(stt._model.transcribe.call_args.kwargs['condition_on_previous_text'])

    def test_full_autonomy_still_blocks_uncertain_voice(self):
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'m.db')
            policy=PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'stop')
            registry=ToolRegistry(policy,db,logging.getLogger('test'))
            handler=Mock(); registry.register(Tool('action','',{'properties':{}},PermissionLevel.ACTION,handler))
            token=CancellationToken(); token.voice_evidence=SimpleNamespace(accepted=False)
            context=registry.cancellation.set(token)
            try: self.assertFalse(registry.execute('action').ok)
            finally: registry.cancellation.reset(context); db.close()
            handler.assert_not_called()

class FakeSessions:
    def __init__(self):
        self.rows={'music':dict(pid=12,process='music',active=True,peak=.5,volume=.37,muted=False),
                   'kk':dict(pid=42,process='kk',active=True,peak=.5,volume=.8,muted=False)}
    def sessions(self): return self.rows
    def set(self,session,volume): session['volume']=volume

class DuckingTests(unittest.TestCase):
    def make(self, folder, backend):
        return AudioFocusTransaction(backend,Path(folder)/'focus.json',.25,0,42)
    def test_exact_restore_and_own_audio_excluded(self):
        with TemporaryDirectory() as folder:
            b=FakeSessions(); t=self.make(folder,b); t.duck()
            self.assertAlmostEqual(b.rows['music']['volume'],.37*.25)
            self.assertEqual(b.rows['kk']['volume'],.8)
            t.restore(); self.assertAlmostEqual(b.rows['music']['volume'],.37)
    def test_manual_volume_not_overwritten(self):
        with TemporaryDirectory() as folder:
            b=FakeSessions(); t=self.make(folder,b); t.duck()
            b.rows['music']['volume']=.51
            t.restore(); self.assertEqual(b.rows['music']['volume'],.51)
    def test_restart_recovers_original_and_missing_session(self):
        with TemporaryDirectory() as folder:
            b=FakeSessions(); t=self.make(folder,b); t.duck()
            self.make(folder,b).recover(); self.assertAlmostEqual(b.rows['music']['volume'],.37)
            t.duck(); del b.rows['music']; self.make(folder,b).recover()
    def test_write_ahead_recovers_interrupted_fade(self):
        with TemporaryDirectory() as folder:
            b=FakeSessions(); t=self.make(folder,b)
            t.owned={'music':dict(original=.37,last=.37,pending=.2,muted=False)}
            t._save(); b.rows['music']['volume']=.2
            self.make(folder,b).recover(); self.assertAlmostEqual(b.rows['music']['volume'],.37)

class FileTerminalTests(unittest.TestCase):
    def test_unicode_paths_exact_replace_and_no_overwrite(self):
        with TemporaryDirectory() as folder:
            p=str(Path(folder)/'Тест с пробелами.txt')
            fs.write(p,'Первый текст')
            with self.assertRaises(FileExistsError): fs.write(p,'overwrite')
            with self.assertRaises(ValueError): fs.replace(p,'missing','new')
            fs.replace(p,'Первый','Второй')
            dst=str(Path(folder)/'копия.txt'); fs.transfer(p,dst)
            self.assertEqual(fs.read_text_file(dst)['content'],'Второй текст')
            self.assertEqual(len(fs.search(folder,'*.txt')['files']),2)
    def test_absolute_paths_and_secrets(self):
        with self.assertRaises(ValueError): fs.absolute('relative.txt')
        with self.assertRaises(PermissionError): fs.absolute('C:/users/example/.ssh/id_rsa')
    def test_command_exit_and_timeout(self):
        with TemporaryDirectory() as folder:
            result=run_terminal('Write-Output hello; exit 7',folder)
            self.assertEqual(result['exit_code'],7); self.assertIn('hello',result['stdout'])
            result=run_terminal('Start-Sleep -Seconds 30',folder,timeout=.3)
            self.assertTrue(result['timed_out']); self.assertLess(result['seconds'],5)
    def test_cancel_running_command(self):
        with TemporaryDirectory() as folder:
            calls=[0]
            def check():
                calls[0]+=1
                if calls[0]>3: raise InterruptedError('cancel')
            with self.assertRaises(InterruptedError): run_terminal('Start-Sleep 30',folder,check=check)

class MonitorTests(unittest.TestCase):
    def test_negative_monitor_and_clamp(self):
        from kk.ui.overlay import select_monitor, placement
        monitors=[dict(monitor_id='left',left=-1920,top=-200,right=0,bottom=880,work_area=[-1920,-200,0,840]),
                  dict(monitor_id='right',left=0,top=0,right=2560,bottom=1440,work_area=[0,0,2560,1400])]
        self.assertEqual(select_monitor(monitors,cursor=(-100,0))['monitor_id'],'left')
        self.assertEqual(placement(monitors[0],(360,160),(3000,5000)),(-360,680))

class CodexTests(unittest.TestCase):
    def test_requires_direct_paid_task_request(self):
        from kk.tools.windows.codex_integration import explicit_task_request
        for text in ('Открой Codex в проекте KK','Покажи результат Codex','Не запускай Codex','Что такое Codex?'):
            self.assertFalse(explicit_task_request(text))
        self.assertTrue(explicit_task_request('Передай эту задачу Codex'))

class AcceptanceTests(unittest.TestCase):
    def test_corpus_category_counts_and_unique_ids(self):
        from kk.voice.acceptance_corpus import corpus,GROUPS
        self.assertEqual({k:len(v) for k,v in GROUPS.items()},dict(ru=30,en=20,mixed=20,technical=20,short=20))
        self.assertEqual(len({r['id'] for r in corpus()}),110)
    def test_metric_known_edit_distance(self):
        from scripts.benchmark_companion_stt import errors
        result=errors('Open Telegram now','Open Firefox now')
        self.assertEqual(result['word_errors'],1)
        self.assertEqual(result['reference_words'],3)

class OrchestratorEvidenceTests(unittest.TestCase):
    def test_unexecuted_success_is_never_spoken(self):
        from kk.core.orchestrator import Orchestrator
        from kk.core.state import StateMachine
        from kk.providers.base import LLMResponse
        class Provider:
            def chat(self,*args): return LLMResponse('Файл создан.',[],{})
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'data'/'m.db')
            registry=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'stop'),db,logging.getLogger('test'))
            registry.register(Tool('observe_context','',{'properties':{}},PermissionLevel.READ,lambda:{}))
            orchestrator=Orchestrator(Provider(),'fake',registry,db,logging.getLogger('test'),StateMachine())
            spoken=[]
            try: answer=orchestrator.respond('Создай файл C:/test.txt',on_token=spoken.append)
            finally: db.close()
            self.assertIn('не выполнено',answer)
            self.assertEqual(spoken,[answer])
            self.assertEqual(orchestrator.last_task.status.value,'FAILED')

if __name__=='__main__': unittest.main()
