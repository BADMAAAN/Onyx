from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import io
import json
import numpy as np

from kk.core.task import AgentTask, CancellationToken
from kk.memory.database import MemoryDatabase
from kk.memory.long_term import LongTermMemory
from kk.voice.speech_queue import SentenceBuffer, SpeechQueue
from kk.voice.wakeword import STTWakeWordProvider, NativeWakeWordProvider
from kk.providers.ollama_provider import OllamaProvider
from kk.vision.screen_understanding import ScreenUnderstanding
from kk.skills.registry import SkillRegistry
from kk.recovery import BackgroundRecoverySupervisor
from kk.core.state import StateMachine, AgentState


class TaskTests(unittest.TestCase):
    def test_full_autonomy_keeps_emergency_stop(self):
        from kk.permissions.policy import PermissionPolicy,PermissionLevel,EmergencyStop
        with TemporaryDirectory() as folder:
            policy=PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'STOP.KK',lambda _:False)
            policy.authorize(PermissionLevel.DANGEROUS,'registered action')
            policy.trigger_stop()
            with self.assertRaises(EmergencyStop):
                policy.authorize(PermissionLevel.READ,'read')

    def test_generation_deadline(self):
        token=CancellationToken(deadline=0)
        with self.assertRaises(TimeoutError):
            token.check()
    def test_cancel_blocks_tool_dispatch(self):
        task=AgentTask('test')
        task.cancellation_token.cancel()
        with self.assertRaises(InterruptedError):
            task.before_action('click',{}, {})
        self.assertEqual(task.actions,[])

    def test_stuck_ignores_timestamps_but_allows_changed_state(self):
        task=AgentTask('test')
        for seconds in [1,2]:
            task.before_action('click',{'name':'Save'},{'seconds':seconds,'title':'editor'})
        with self.assertRaisesRegex(RuntimeError,'Stuck'):
            task.before_action('click',{'name':'Save'},{'seconds':3,'title':'editor'})
        task.before_action('click',{'name':'Save'},{'title':'dialog'})

    def test_timeout_and_step_limit(self):
        task=AgentTask('test',max_steps=1)
        task.before_action('x',{}, {})
        with self.assertRaisesRegex(RuntimeError,'limit'):
            task.before_action('y',{}, {})
        task.timeout_seconds=-1
        with self.assertRaises(TimeoutError):
            task.check()


class RecoveryTests(unittest.TestCase):
    def make_supervisor(self):
        state=StateMachine(); context=Mock(); provider=Mock(); provider.is_available.return_value=True
        runtime=SimpleNamespace(state=state,context=context,provider=provider)
        loop=Mock(); stt=SimpleNamespace(cancel=Mock(),cancel_event=Event())
        voice=SimpleNamespace(loop=loop,stt=stt)
        return BackgroundRecoverySupervisor(runtime,voice,'Focusrite',resume_gap=10),runtime,voice

    def test_microphone_disconnect_and_reconnect(self):
        supervisor,runtime,voice=self.make_supervisor()
        monitors=[{'monitor_id':'D1','left':0,'top':0,'right':1,'bottom':1,'dpi':{'x':96}}]
        present=[{'name':'Focusrite USB','input_channels':2,'default_input':True}]
        supervisor.tick(now=1,audio_devices=present,monitors=monitors)
        supervisor.tick(now=2,audio_devices=[],monitors=monitors)
        self.assertEqual(runtime.state.state,AgentState.ERROR)
        voice.loop.interrupt.assert_called()
        supervisor.tick(now=3,audio_devices=present,monitors=monitors)
        self.assertEqual(runtime.state.state,AgentState.IDLE)
        self.assertEqual([e['event'] for e in supervisor.events[-2:]],
                         ['microphone_disconnected','microphone_reconnected'])

    def test_monitor_change_resets_stale_context(self):
        supervisor,runtime,_=self.make_supervisor()
        audio=[{'name':'Focusrite','input_channels':2}]
        one=[{'monitor_id':'D1','left':0,'top':0,'right':1,'bottom':1,'dpi':{'x':96}}]
        two=one+[{'monitor_id':'D2','left':1,'top':0,'right':2,'bottom':1,'dpi':{'x':120}}]
        supervisor.tick(now=1,audio_devices=audio,monitors=one)
        supervisor.tick(now=2,audio_devices=audio,monitors=two)
        runtime.context.reset.assert_called_with('monitor_topology_changed')

    def test_resume_gap_cancels_obsolete_work_and_refreshes_context(self):
        supervisor,runtime,voice=self.make_supervisor()
        supervisor.last_tick=1
        supervisor.tick(now=20,audio_devices=[{'name':'Focusrite','input_channels':2}],monitors=[])
        voice.loop.interrupt.assert_called()
        voice.stt.cancel.assert_called()
        runtime.context.reset.assert_called_with('resume')
        runtime.provider.is_available.assert_called()


class MemoryTests(unittest.TestCase):
    def test_relevance_corrections_and_cross_thread_use(self):
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'m.db')
            db.remember('telegram','Открывать Telegram Desktop')
            self.assertEqual(LongTermMemory(db).context('совершенно иное'), '')
            self.assertIn('Telegram',LongTermMemory(db).context('открой Telegram'))
            errors=[]
            def work():
                try:
                    db.remember('thread','yes')
                except Exception as exc:
                    errors.append(exc)
            t=Thread(target=work); t.start(); t.join()
            self.assertEqual(errors,[])
            db.add_correction('editor','кнопка сохранить','left','right',{})
            self.assertEqual(len(db.relevant_corrections('editor','сохранить')),1)
            self.assertEqual(db.relevant_corrections('browser','сохранить'),[])
            db.close()

    def test_skill_needs_confirmation_and_parameterizes_text(self):
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'m.db')
            skills=SkillRegistry(Path(folder)/'skills'/'pending',db)
            task=AgentTask('Открой блокнот и напиши текст')
            task.actions=[{'tool':'type_text','arguments':{'text':'private message'},'result':{'ok':True}}]
            candidate=skills.propose_task(task)
            self.assertEqual(skills.retrieve('блокнот'),[])
            self.assertEqual(candidate['steps'][0]['arguments']['text'],'{text}')
            skills.confirm(candidate['name'])
            self.assertEqual(len(skills.retrieve('блокнот')),1)
            skills.record_execution(candidate['name'],True)
            self.assertEqual(skills.retrieve('блокнот')[0]['statistics']['successes'],1)
            from kk.core.task import TaskStatus
            task.status=TaskStatus.FAILED
            with self.assertRaises(ValueError):
                skills.propose_task(task)
            db.close()


class PerceptionTests(unittest.TestCase):
    def test_text_uses_uia_without_capture_or_vision(self):
        capture=Mock(); provider=Mock()
        screen=ScreenUnderstanding(capture,Mock(),provider)
        screen.fast_context=lambda:{'ui_elements':[{'text':'документ 7429'}],'source':'uia'}
        result=screen.see_screen(question='Что написано в окне?')
        self.assertFalse(result['vision_used'])
        provider.answer_about_screen.assert_not_called()
        capture.capture_image.assert_not_called()

    def test_native_missing_model_does_not_download(self):
        with self.assertRaises(FileNotFoundError):
            NativeWakeWordProvider('nonexistent/key_key.onnx')

    def test_wakeword_boundaries_and_hyphen(self):
        detector=STTWakeWordProvider()
        for text in ['кей','окей','кейси','эй кей','окей кей','эй эй','обычная русская речь']:
            self.assertFalse(detector.detect_text(text),text)
        for text in ['кей кей','Кей-кей, привет']:
            self.assertTrue(detector.detect_text(text),text)
        self.assertEqual(detector.command('Кей-кей, привет'),'привет')


class StreamingTests(unittest.TestCase):
    def test_live_stt_passes_pcm_in_memory_without_tempfile(self):
        from kk.voice.stt_faster_whisper import FasterWhisperSTT
        stt=FasterWhisperSTT()
        samples=np.zeros(1600,dtype=np.float32)
        stt.record_until_silence=Mock(return_value=samples)
        segments=[SimpleNamespace(text='Кей Кей, привет',start=0,end=.1,
            avg_logprob=-.1,no_speech_prob=.01,compression_ratio=1,words=[])]
        info=SimpleNamespace(language='ru',language_probability=1,duration=.1)
        model=Mock(); model.transcribe.return_value=(segments,info)
        stt._model=model
        with patch('kk.voice.stt_faster_whisper.tempfile.NamedTemporaryFile',side_effect=AssertionError('disk write')):
            self.assertEqual(stt.listen().text,'Кей Кей, привет')
        self.assertIs(model.transcribe.call_args.args[0],samples)
        self.assertEqual(model.transcribe.call_args.kwargs['beam_size'],5)
        self.assertTrue(stt.record_until_silence.call_args.kwargs['in_memory'])

    def test_tts_prewarm_runs_inference_once_per_service(self):
        from kk.voice.tts_silero import SileroTTS
        tts=SileroTTS(Path('unused'),'unused')
        tts.synthesize=Mock(return_value=np.zeros(10))
        tts.prewarm(); calls=tts.synthesize.call_count
        self.assertGreaterEqual(calls,2)
        tts.prewarm()
        self.assertEqual(tts.synthesize.call_count,calls)

    def test_sentences_arrive_before_final_flush(self):
        chunks=[]
        buf=SentenceBuffer(chunks.append,minimum=10)
        buf.feed('Да. Сейчас открыт ')
        self.assertEqual(chunks,[])
        buf.feed('Блокнот. Затем ')
        self.assertEqual(chunks,['Да. Сейчас открыт Блокнот.'])
        buf.feed('настройки.')
        buf.flush()
        self.assertEqual(chunks[-1],'Затем настройки.')

    def test_streaming_tokens_and_unique_calls(self):
        events=[{'message':{'content':'Привет. '}}, {'message':{'content':'Готов.'}},
            {'message':{'tool_calls':[{'function':{'name':'get_volume','arguments':{}}}]}},
            {'message':{'tool_calls':[{'function':{'name':'list_windows','arguments':{}}}]}}, {'done':True}]
        raw=io.BytesIO(b'\n'.join(json.dumps(x).encode() for x in events))
        chunks=[]
        with patch('kk.providers.ollama_provider.urlopen',return_value=raw):
            response,timing=OllamaProvider('http://local').chat_stream('model',[],on_token=chunks.append)
        self.assertEqual(chunks,['Привет. ','Готов.'])
        self.assertEqual(len({c.id for c in response.tool_calls}),2)
        self.assertGreaterEqual(timing['total_seconds'],timing['first_token_seconds'])

    def test_cancel_after_uncancellable_synthesis_never_plays_old_audio(self):
        entered,release=Event(),Event()
        def synth(text):
            entered.set(); release.wait(2)
            return np.zeros(160,dtype=np.float32)
        tts=SimpleNamespace(muted=False,volume=1,synthesize=synth,output_device=None,sample_rate=16000,performance=None)
        with patch('kk.voice.speech_queue.sd.stop'),patch('kk.voice.speech_queue.sd.play') as play:
            queue=SpeechQueue(tts)
            token=queue.begin()
            queue.submit('old',token)
            self.assertTrue(entered.wait(2))
            queue.cancel()
            release.set()
            queue.close()
            play.assert_not_called()

    def test_stream_cancel_discards_result(self):
        token=CancellationToken(); token.cancel()
        raw=io.BytesIO(b'{"message":{"content":"old"}}\n')
        with patch('kk.providers.ollama_provider.urlopen',return_value=raw):
            with self.assertRaises(InterruptedError):
                OllamaProvider('http://local').chat_stream('model',[],cancellation=token)


if __name__=='__main__':
    unittest.main()
