import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from kk.core.desktop_commands import parse_plan, run_desktop, DesktopContext
from kk.core.decision_policy import DecisionPolicy
from kk.core.fast_commands import run_fast, normalize
from kk.core.state import StateMachine
from kk.core.task import AgentTask, TaskStatus
from kk.tools.registry import ToolResult
from kk.tools.windows.browser import address_matches
from kk.voice.wakeword import STTWakeWordProvider


class OnyxTests(TestCase):
    def test_page_text_cannot_impersonate_browser_address(self):
        from kk.tools.windows.browser import address_element
        self.assertFalse(address_element({'control_type':'EditControl','name':'Message','value':'https://youtube.com'}))
        self.assertTrue(address_element({'control_type':'ComboBoxControl','automation_id':'urlbar-input'}))
        self.assertFalse(address_element({'control_type':'ComboBoxControl','automation_id':'urlbar-input','offscreen':True}))

    def test_public_name_keeps_transcripts_out_of_technical_log(self):
        from kk.logging_setup import RedactingFilter
        for message in ['[Onyx] User: private words','[Onyx] private answer','[KK] User: private words']:
            record=logging.LogRecord('test',20,'',0,message,(),None)
            RedactingFilter().filter(record)
            self.assertNotIn('private',record.getMessage())
            self.assertIn('REDACTED',record.getMessage())

    def test_render_reference_echo_gate_preserves_double_talk(self):
        import numpy as np
        from threading import Event,RLock
        from kk.voice.speech_queue import SpeechQueue
        rng=np.random.default_rng(47)
        reference=rng.normal(0,.12,8000).astype(np.float32)
        speech=SpeechQueue.__new__(SpeechQueue)
        speech.lock=RLock();speech.playing=Event();speech.playing.set()
        speech.reference=reference;speech.reference_started=0
        pcm=(reference[-480:]*.4*32768).astype(np.int16).tobytes()
        with patch('kk.voice.speech_queue.perf_counter',return_value=.5):
            self.assertTrue(speech.is_echo(pcm))
            mixed=reference[-480:]*.4+rng.normal(0,.06,480)
            self.assertFalse(speech.is_echo((mixed*32768).astype(np.int16).tobytes()))
            quiet_user=reference[-480:]*.4+rng.normal(0,.01,480)
            self.assertFalse(speech.is_echo((quiet_user*32768).astype(np.int16).tobytes()))

    def test_uia_filters_offscreen_geometry_and_defers_virtual_history(self):
        from kk.vision.uia import UIAutomationService
        viewport=dict(left=0,top=0,right=100,bottom=100)
        item=dict(rect=dict(left=10,top=-200,right=80,bottom=-100),offscreen=False)
        UIAutomationService._clip(item,viewport)
        self.assertTrue(item['offscreen'])
        self.assertTrue(UIAutomationService._virtual_history({'class_name':'class HistoryInner'}))
        self.assertFalse(UIAutomationService._virtual_history({'class_name':'class Dialogs::InnerWidget'}))

    def test_no_action_question_cannot_complete_action_task(self):
        from kk.core.orchestrator import Orchestrator
        from kk.providers.base import LLMResponse
        from kk.memory.database import MemoryDatabase
        from kk.permissions.policy import PermissionPolicy, PermissionLevel
        from kk.tools.registry import ToolRegistry
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'memory.db')
            try:
                tools=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'STOP'),db,logging.getLogger('test'))
                tools.execute=Mock(return_value=ToolResult(True,{}))
                provider=SimpleNamespace(chat=lambda *args: LLMResponse('Подтверди намерение?',[]))
                agent=Orchestrator(provider,'test',tools,db,logging.getLogger('test'),StateMachine())
                with patch('kk.core.fast_commands.run_fast',return_value=None):
                    agent.respond('Включи последний добавленный трек')
                self.assertEqual(agent.last_task.status,TaskStatus.FAILED)
            finally: db.close()

    def test_preferences_survive_restart(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'preferences.json'
            policy=DecisionPolicy({'browser':'Firefox'},path=path)
            policy.remember('browser','Chrome')
            loaded=DecisionPolicy({'browser':'Firefox'},path=path)
            self.assertEqual(loaded.application('браузер'),'Chrome')

    def test_benchmark_provenance_and_error_counts(self):
        from kk.voice.provider_benchmark import errors,load_manifest
        self.assertEqual(errors('Открой Firefox','открой Firefox.'),(0,2))
        self.assertEqual(errors('раз два три','раз три'),(1,3))
        with TemporaryDirectory() as folder:
            path=Path(folder)/'corpus.json'
            path.write_text('[{"audio":"secret.wav","source":"ambient"}]',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'provenance'): load_manifest(path)

    def test_start_recording_never_calls_send(self):
        from kk.tools.windows.telegram import TelegramActions
        registry=SimpleNamespace(check_cancel=Mock())
        telegram=TelegramActions(registry,Mock())
        telegram.focus=Mock(return_value={'hwnd':42,'executable':'AyuGram.exe'})
        telegram.press_command=Mock()
        telegram.observe=Mock(side_effect=[
            [{'automation_id':'HistoryWidget.SendButton','name':'Запись голосового сообщения'}],
            [{'automation_id':'HistoryWidget.RecordLock','name':'Продолжать запись автоматически'}]])
        result=telegram.start_recording()
        self.assertTrue(result['verified']);self.assertFalse(result['sent'])
        telegram.press_command.assert_called_once_with({'hwnd':42,'executable':'AyuGram.exe'},'record_voice')

    def test_send_requires_explicit_separate_request(self):
        from kk.tools.windows.telegram import TelegramActions
        for request in ['Начни записывать голосовое','Не отправь','не отправляй','']:
            registry=SimpleNamespace(cancellation=SimpleNamespace(get=lambda:SimpleNamespace(user_request=request)))
            telegram=TelegramActions(registry,Mock());telegram.recording_hwnd=42
            with self.assertRaises(PermissionError):telegram.finish(send=True)

    def test_first_chat_fallback_does_not_launch_app_named_first_chat(self):
        tools=SimpleNamespace(execute=Mock(return_value=ToolResult(True,{'verified':False})))
        self.assertIsNone(run_fast('открой первый чат',AgentTask('test'),tools,StateMachine()))
        self.assertEqual(tools.execute.call_count,1)
        self.assertIsNone(parse_plan('открой первый чат'))

    def test_uia_failure_falls_back_to_ocr(self):
        from kk.vision.screen_understanding import ScreenUnderstanding
        from PIL import Image
        screen=ScreenUnderstanding(Mock(),Mock(),None)
        screen.uia.tree.side_effect=RuntimeError('UIA unavailable')
        screen.ocr=Mock()
        screen.ocr.read.return_value={'seconds':0,'results':[{'text':'Ошибка 42'}]}
        screen.capture.capture_image.return_value=(Image.new('RGB',(5,5)),{})
        with patch('kk.vision.screen_context.current_screen_context',return_value={'foreground':{}}),patch.object(screen,'_mouse',return_value=(0,0)):
            result=screen.see_screen(question='Что это за ошибка?')
        self.assertEqual(result['description'],'Ошибка 42')
        self.assertIn('UIA unavailable',result['uia_error'])

    def test_monitor_crop_does_not_reuse_other_window_uia(self):
        from kk.vision.screen_understanding import ScreenUnderstanding
        from PIL import Image
        screen=ScreenUnderstanding(Mock(),Mock(),None)
        screen.fast_context=lambda:{'foreground':{},'ui_elements':[{'text':'WRONG MONITOR'}]}
        screen.ocr=Mock()
        screen.ocr.read.return_value={'seconds':0,'results':[{'text':'Correct monitor'}]}
        screen.capture.capture_image.return_value=(Image.new('RGB',(5,5)),{})
        result=screen.see_screen(question='Прочитай текст',region=[1920,0,3840,1080])
        self.assertEqual(result['description'],'Correct monitor')

    def test_wake_aliases_and_bare_wake(self):
        detector = STTWakeWordProvider('оникс')
        for wake in ['Оникс', 'Onyx', 'Кей Кей']:
            self.assertEqual(normalize(wake+', открой TG.'), 'открой TG')
            self.assertTrue(detector.detect_text(wake))
            self.assertEqual(detector.command(wake), '')
            tools = SimpleNamespace(execute=Mock())
            self.assertEqual(run_fast(wake, AgentTask(wake), tools, StateMachine()), '')
            tools.execute.assert_not_called()
        self.assertFalse(detector.detect_text('ониксовый'))

    def test_natural_open_matrix(self):
        for phrase, kinds in [
            ('Оникс, открой Telegram.', ['open']),
            ('Оникс, открой Firefox, открой YouTube.', ['open','url']),
            ('Открой TG.', ['open']), ('Открой Яндекс Музыку.', ['open']),
            ('Открой DS на full.', ['open','window']),
            ('Открой браузер и GPT.', ['open','url']),
            ('Оникс, Firefox и YouTube.', ['open','url']),
            ('Зайди на YouTube.', ['url']), ('Хочу поиграть в Hearthstone.', ['game']),
            ('Запусти телегу.', ['open'])]:
            with self.subTest(phrase=phrase):
                self.assertEqual([s['kind'] for s in parse_plan(phrase)], kinds)

    def test_default_monitor_and_preferences(self):
        policy=DecisionPolicy({'telegram_client':'AyuGram','browser':'Firefox'})
        monitors=[{'index':1,'primary':True},{'index':2,'primary':False}]
        self.assertEqual(policy.application('Telegram'), 'AyuGram')
        self.assertEqual(policy.application('браузер'), 'Firefox')
        self.assertEqual(policy.application('Telegram Desktop'), 'Telegram Desktop')
        self.assertEqual(policy.monitor(monitors),1)
        self.assertEqual(policy.monitor(monitors,foreground={'monitor_index':2}),2)
        context=DesktopContext('TG',12,monotonic(),last_monitor=2)
        self.assertEqual(policy.monitor(monitors,context,{'monitor_index':1}),1)
        self.assertEqual(policy.monitor(monitors,context,explicit=1),1)
        with self.assertRaises(LookupError): policy.monitor(monitors,explicit=3)
        self.assertEqual(policy.clarification_budget(.95),0)
        self.assertEqual(policy.clarification_budget(.7,True),0)
        self.assertEqual(policy.clarification_budget(.3),1)

    def test_window_followups_same_hwnd(self):
        calls=[]
        def execute(name,args):
            calls.append((name,args))
            return ToolResult(True,{'verified':True,'hwnd':42,'monitor_index':1,'after':{'monitor_index':2}})
        tools=SimpleNamespace(execute=execute,personal_intents=None)
        context=DesktopContext()
        for command in ['Открой TG на full','Сделай его на full','Перенеси на второй']:
            run_desktop(command,AgentTask(command),tools,StateMachine(),context)
        self.assertEqual([c for c in calls if c[0]=='open_application'], [('open_application',{'name':'telegram'})])
        self.assertTrue(all(args['hwnd']==42 for name,args in calls if name!='open_application'))
        self.assertEqual(context.last_monitor,2)

    def test_browser_choice_propagates(self):
        calls=[]
        def execute(name,args):
            calls.append((name,args))
            return ToolResult(True,{'verified':True,'hwnd':42,'opened':True})
        tools=SimpleNamespace(execute=execute,personal_intents=None)
        run_desktop('Открой Firefox, открой YouTube',AgentTask('test'),tools,StateMachine(),DesktopContext())
        self.assertEqual(calls[-1],('open_url',{'url':'https://www.youtube.com','browser':'Firefox'}))

    def test_browser_success_requires_address_evidence(self):
        tools=SimpleNamespace(execute=Mock(return_value=ToolResult(True,{'opened':True})),personal_intents=None)
        task=AgentTask('GPT')
        answer=run_desktop('Открой GPT',task,tools,StateMachine(),DesktopContext())
        self.assertIn('не подтверждена',answer)
        self.assertEqual(task.status,TaskStatus.FAILED)
        self.assertTrue(address_matches('https://www.youtube.com','https://youtube.com/'))
        self.assertFalse(address_matches('https://youtube.com','https://youtube.com.attacker.test/'))
        self.assertFalse(address_matches('https://youtube.com/results?search_query=x','https://youtube.com/'))

    def test_two_monitor_answers_are_scoped_and_nonempty(self):
        def execute(name,args):
            data = ([{'index':1},{'index':2}] if name=='get_monitors' else
                    [{'monitor_index':1,'title':'First'},{'monitor_index':2,'title':'Second'}])
            return ToolResult(True,data)
        tools=SimpleNamespace(execute=execute)
        for phrase, expected, other in [('Что на первом экране?','First','Second'),('Что на втором?','Second','First')]:
            answer=run_fast(phrase,AgentTask(phrase),tools,StateMachine())
            self.assertIn(expected,answer); self.assertNotIn(other,answer)

    def test_failed_media_replans_without_acknowledgment(self):
        tools=SimpleNamespace(execute=Mock(return_value=ToolResult(True,{'verified':False})))
        task=AgentTask('Включи музыку')
        self.assertIsNone(run_fast(task.user_request,task,tools,StateMachine()))
        self.assertTrue(task.actions)
        tools.execute.return_value=ToolResult(True,{'verified':True,'status':4})
        self.assertEqual(run_fast(task.user_request,AgentTask(task.user_request),tools,StateMachine()),'Включила.')

    def test_known_alias_discovery_is_not_bad_audio(self):
        from kk.voice.recognition import suspicion
        from kk.voice.stt_faster_whisper import Transcription
        resolver=SimpleNamespace(inspect=lambda _: {'found':False})
        for phrase in ['Оникс, открой Telegram','Оникс, открой Firefox, открой YouTube','Открой Яндекс Музыку']:
            heard=Transcription(phrase,'ru',1,2,.1)
            self.assertEqual(suspicion(heard,resolver),[])

    def test_general_question_does_not_observe_desktop(self):
        from kk.core.orchestrator import Orchestrator
        from kk.providers.base import LLMResponse
        from kk.memory.database import MemoryDatabase
        from kk.permissions.policy import PermissionPolicy, PermissionLevel
        from kk.tools.registry import ToolRegistry
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'memory.db')
            try:
                tools=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'STOP'),db,logging.getLogger('test'))
                tools.execute=Mock(side_effect=AssertionError('Ordinary conversation must not inspect desktop'))
                provider=SimpleNamespace(chat=lambda *args: LLMResponse('Из-за рассеяния света.',[]))
                agent=Orchestrator(provider,'test',tools,db,logging.getLogger('test'),StateMachine())
                self.assertIn('рассеяния',agent.respond('Почему небо синее?'))
                tools.execute.assert_not_called()
            finally: db.close()
