from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import logging
import json

from kk.core.brain import BrainRouter, ToolCapabilityRouter
from kk.core.literal_intents import literal_intent
from kk.core.task import AgentTask, CancellationToken
from kk.providers.resources import ModelResourceManager, PressureGuard, ResourcePressure

GB = 1024**3


class MasterBuildTests(TestCase):
    def test_literal_preserves_case_punctuation_and_ifconfig(self):
        for payload in ['ifconfig', 'Write-Host "Hello, GPT!"', 'Hello. World!']:
            value=literal_intent('Открой PowerShell и напиши '+payload)
            self.assertEqual(value,dict(app='PowerShell',text=payload,submit=False))
        self.assertTrue(literal_intent('Открой PowerShell и выполни ipconfig')['submit'])
        self.assertIsNone(literal_intent('Расскажи про Firefox'))

    def test_literal_quoted_payload_is_not_split_into_actions(self):
        self.assertEqual(literal_intent('Напиши «Firefox и YouTube, открой TG»')['text'], 'Firefox и YouTube, открой TG')

    def test_brain_router_uses_configured_models_and_escalates_replan(self):
        router=BrainRouter('measured-fast','measured-deep')
        self.assertEqual(router.choose('Расскажи про ChatGPT').model,'measured-fast')
        self.assertEqual(router.choose('Разберись, почему проект не собирается').model,'measured-deep')
        self.assertEqual(router.choose('Открой Firefox',1).tier,'deep')

    def test_tool_router_codex_draft_does_not_advertise_paid_cli_or_telegram(self):
        names=['open_application','run_codex_task','open_codex','open_first_telegram_chat','type_text','observe_context']
        catalog=[{'function':{'name':name}} for name in names]
        selected=ToolCapabilityRouter().select(catalog,'Открой Codex и напиши в последнем чате "исправь тесты"')
        result={s['function']['name'] for s in selected}
        self.assertIn('open_application',result)
        self.assertIn('type_text',result)
        self.assertFalse(result & {'run_codex_task','open_codex','open_first_telegram_chat'})

    def test_barge_in_only_cancels_speech(self):
        from kk.voice.loop import VoiceLoop
        loop=VoiceLoop.__new__(VoiceLoop)
        loop.generation=CancellationToken()
        loop.agent=Mock(); loop.speech=Mock()
        loop.interrupt_speech()
        self.assertFalse(loop.generation.event.is_set())
        loop.agent.cancel.assert_not_called()
        loop.speech.cancel.assert_called_once()
        loop.interrupt()
        self.assertTrue(loop.generation.event.is_set())
        loop.agent.cancel.assert_called_once()

    def test_full_multisentence_answer_survives_sentence_buffer(self):
        from kk.voice.speech_queue import SentenceBuffer
        expected='ChatGPT — языковая модель. Она отвечает на вопросы и помогает разбирать задачи. Для проверки фактов полезно обращаться к источникам. '
        chunks=[]
        buffer=SentenceBuffer(chunks.append)
        for word in expected.split(' '): buffer.feed(word+' ')
        buffer.flush()
        self.assertEqual(' '.join(' '.join(chunks).split()),' '.join(expected.split()))
        self.assertGreaterEqual(len(chunks),2)

    def test_uncertain_explanation_never_returns_bare_gpt(self):
        from kk.voice.recognition import fragment_question
        result=SimpleNamespace(text='Расскажи про ChatGPT', words=[dict(word='GPT',probability=.2)])
        self.assertIn('вопрос', fragment_question(result))

    def test_resource_reserve_cannot_be_lowered_below_eight_gb(self):
        manager=ModelResourceManager(Mock(),ram_reserve_gb=2)
        self.assertEqual(manager.ram_reserve,8*GB)

    def manager(self, available=20*GB, model_size=9*GB):
        provider=SimpleNamespace(base_url='http://localhost',context_window=8192,
            list_models=lambda:[dict(name='fast',size=model_size)], unload_model=Mock())
        manager=ModelResourceManager(provider)
        manager.snapshot=Mock(return_value=dict(ram_total=32*GB,ram_available=available,vram_total=16*GB,vram_used=2*GB,models=[]))
        return manager

    def test_resource_preflight_rejects_missing_telemetry(self):
        manager=self.manager()
        manager.snapshot.return_value.pop('vram_total')
        with self.assertRaises(ResourcePressure): manager.preflight('fast')

    def test_resource_preflight_rejects_low_ram_before_inference(self):
        manager=self.manager(available=8*GB)
        with self.assertRaisesRegex(ResourcePressure,'9 GB'): manager.preflight('fast')

    def test_resource_preflight_rejects_model_that_consumes_vram_reserve(self):
        manager=self.manager(model_size=20*GB)
        with patch('kk.providers.resources.responsiveness',return_value={'responsive':True}):
            with self.assertRaisesRegex(ResourcePressure,'VRAM reserve'): manager.preflight('fast')

    def test_resource_preflight_rejects_unresponsive_desktop(self):
        with patch('kk.providers.resources.responsiveness',return_value={'responsive':False}):
            with self.assertRaisesRegex(ResourcePressure,'unresponsive'): self.manager().preflight('fast')

    def test_resource_watchdog_aborts_network_before_ram_reserve_is_lost(self):
        manager=self.manager()
        with patch('kk.providers.resources.responsiveness',return_value={'responsive':True,'milliseconds':1}):
            guard=PressureGuard(manager,'fast')
            guard.abort_io=Mock()
            guard.done.wait=Mock(return_value=False)
            with patch('kk.providers.resources.psutil.virtual_memory',return_value=SimpleNamespace(available=7*GB,percent=79)):
                guard.watch()
            self.assertTrue(guard.aborted.is_set())
            guard.abort_io.assert_called_once()
            with self.assertRaises(ResourcePressure): guard.check()

    def test_resource_watchdog_rejects_pagefile_growth(self):
        manager=self.manager()
        with patch('kk.providers.resources.responsiveness',return_value={'responsive':True,'milliseconds':1}):
            guard=PressureGuard(manager,'fast'); guard.done.wait=Mock(return_value=False)
            with (patch('kk.providers.resources.psutil.virtual_memory',return_value=SimpleNamespace(available=15*GB,percent=55)),
                  patch('kk.providers.resources.psutil.swap_memory',return_value=SimpleNamespace(used=guard.swap_start+GB))):
                guard.watch()
            self.assertIn('Pagefile',guard.reason)

    def test_unload_must_be_observed_before_next_model(self):
        manager=self.manager()
        with patch('kk.providers.resources.request_json',return_value={'models':[]}):
            manager.ensure_unloaded('fast')
        manager.provider.unload_model.assert_called_once_with('fast')

    def test_failed_video_cannot_complete_after_browser_launch(self):
        from kk.core.completion import review
        task=AgentTask('Открой Firefox и включи любое видео')
        task.actions=[dict(tool='open_application',arguments={'name':'Firefox'},result=dict(ok=True,data={'verified':True}))]
        provider=Mock()
        self.assertFalse(review(provider,'test',task,'Включила.')[0])
        provider.chat.assert_not_called()

    def test_partial_speech_cannot_type_or_submit(self):
        from kk.tools.registry import ToolRegistry,Tool
        from kk.permissions.policy import PermissionPolicy, PermissionLevel
        from kk.memory.database import MemoryDatabase
        with TemporaryDirectory() as folder:
            memory=MemoryDatabase(Path(folder)/'test.db')
            tools=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'stop'),memory,logging.getLogger('test'))
            handler=Mock()
            tools.register(Tool('text_entry','type',{'type':'object','properties':{'text':{'type':'string'},'submit':{'type':'boolean'}}},PermissionLevel.ACTION,handler))
            token=CancellationToken(); token.user_request='Напиши ifconfig'
            token.voice_evidence=SimpleNamespace(committed=False,accepted=True)
            context=tools.cancellation.set(token)
            try: self.assertFalse(tools.execute('text_entry',{'text':'ifconfig'}).ok)
            finally: tools.cancellation.reset(context)
            handler.assert_not_called(); memory.close()

    def test_original_goal_survives_revision(self):
        task=AgentTask('Открой Firefox')
        task.user_request+='\nУточнение: на втором экране'
        self.assertEqual(task.original_request,'Открой Firefox')
        self.assertIn('втором',task.goal)

    def test_uia_surrogate_does_not_corrupt_utf8_report(self):
        from kk.vision.uia import clean_text
        self.assertEqual(clean_text('text\ud83d').encode('utf-8'),b'text?')

    def test_music_review_rejects_stale_playback_after_pause_key(self):
        from kk.core.completion import review
        task=AgentTask('Включи музыку')
        task.actions=[dict(tool='get_current_media',arguments={},result=dict(ok=True,data={
            'session_id':'Spotify','playing':True})),dict(tool='press_hotkey',arguments={'keys':['space']},result=dict(ok=True,data={}))]
        self.assertFalse(review(Mock(),'test',task,'Готово')[0])

    def test_music_review_rejects_unrelated_browser_video(self):
        from kk.core.completion import review
        task=AgentTask('Включи музыку')
        task.actions=[dict(tool='get_current_media',arguments={},result=dict(ok=True,data={
            'session_id':'firefox','playing':True}))]
        self.assertFalse(review(Mock(),'test',task,'Готово')[0])

    def test_resource_telemetry_failure_aborts_instead_of_losing_watchdog(self):
        guard=PressureGuard(self.manager(),'fast')
        guard.done.wait=Mock(return_value=False)
        guard.abort_io=Mock()
        with patch('kk.providers.resources.psutil.virtual_memory',side_effect=OSError('telemetry')):
            guard.watch()
        self.assertTrue(guard.aborted.is_set())
        guard.abort_io.assert_called_once()

    def test_incomplete_ollama_stream_cannot_be_a_successful_answer(self):
        from kk.providers.ollama_provider import OllamaProvider
        from kk.providers.base import ProviderError
        from contextlib import contextmanager
        @contextmanager
        def stream(*args,**kwargs):
            yield [b'{"message":{"content":"GPT"}}\n']
        with patch('kk.providers.ollama_provider.interruptible_request',stream):
            with self.assertRaisesRegex(ProviderError,'before done'):
                OllamaProvider('http://test').chat_stream('test',[])

    def test_browser_planner_can_click_observed_controls(self):
        catalog=[{'function':{'name':name}} for name in ['browser_control','click_ui_element','find_ui_element','get_ui_tree']]
        selected=ToolCapabilityRouter().select(catalog,'Открой Firefox и включи видео')
        self.assertEqual(selected,catalog)

    def test_context_compaction_keeps_complete_tool_rounds_and_original_goal(self):
        from kk.core.brain import ContextBroker
        task=AgentTask('Найди и перенеси файл')
        messages=[{'role':'system','content':'system'},{'role':'user','content':task.goal}]
        for i in range(6):
            messages.extend([{'role':'assistant','tool_calls':[{'id':str(i)}]},
                             {'role':'tool','tool_call_id':str(i),'content':'x'*100}])
        # Serialized roles/call IDs alone exceed 100 characters: the former
        # content-only cap silently sent an oversized request to the model.
        with self.assertRaisesRegex(ValueError, 'context budget'):
            ContextBroker().compact_rounds(messages,task,budget=100)
        compact=ContextBroker().compact_rounds(messages,task,budget=1200)
        self.assertEqual(compact[2]['content'],task.goal)
        self.assertEqual([m['tool_call_id'] for m in compact if m['role']=='tool'],['3','4','5'])
        self.assertLessEqual(len(__import__('json').dumps(compact,ensure_ascii=False)),1200)

    def test_browser_playback_rejects_other_window_even_same_process(self):
        from kk.tools.windows.browser import BrowserController
        media=Mock()
        browser=BrowserController(Mock(),Mock(),media)
        browser.target_hwnd=123
        result=browser.playback({'hwnd':456,'process':'firefox.exe'})
        self.assertFalse(result['verified'])
        media.call.assert_not_called()

    def test_telegram_header_buttons_do_not_replace_conversation_name(self):
        from kk.tools.windows.telegram import TelegramActions
        screen=Mock()
        screen.capture.capture_image.return_value=(Mock(),{})
        screen.ocr.read.return_value={'results':[{'lines':[{'text':'Test Contact'}]}]}
        rect=dict(left=0,top=0,right=500,bottom=60,center_x=250,center_y=30)
        header=dict(class_name='class HistoryView::TopBarWidget',rect=rect)
        button=dict(control_type='ButtonControl',name='Call',rect=rect)
        self.assertEqual(TelegramActions(Mock(),Mock(),screen)._header_text([header,button]),'Test Contact')

    def test_tts_keeps_distinct_pronunciation_for_network_commands(self):
        from kk.voice.prosody import prepare
        self.assertEqual(prepare('ifconfig, ipconfig'),'иф конфиг, ай пи конфиг')

    def test_deadline_aborts_socket_while_waiting_for_model_headers(self):
        from kk.providers.ollama_provider import interruptible_request
        from urllib.request import Request
        from threading import Event
        from time import monotonic
        stopped=Event()
        connection=Mock()
        connection.sock.shutdown.side_effect=lambda *args:stopped.set()
        def headers():
            if not stopped.wait(2): self.fail('Deadline did not interrupt inference')
            raise OSError('socket interrupted')
        connection.getresponse.side_effect=headers
        token=CancellationToken(deadline=monotonic()+.1)
        with patch('http.client.HTTPConnection',return_value=connection):
            with self.assertRaises(TimeoutError):
                with interruptible_request(Request('http://local/api/chat',data=b'{}'),10,Mock(),token):
                    self.fail('Unexpected successful response')
        connection.close.assert_called_once()

    def test_literal_terminal_multiline_does_not_press_keys(self):
        from kk.tools.windows.text_entry import TextEntry
        entry=TextEntry(Mock(),Mock())
        with (patch('kk.tools.windows.text_entry.foreground_window',return_value={'hwnd':42,'process':'pwsh.exe','title':'PowerShell'}),
              patch('kk.tools.windows.text_entry.type_text') as typing):
            with self.assertRaisesRegex(ValueError,'Многострочный'):
                entry.enter('first\nsecond',submit=False)
        typing.assert_not_called()
