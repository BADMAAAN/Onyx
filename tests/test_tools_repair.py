"""Regressions for target binding, exact dictation and observable completion."""
import logging
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from kk.core.decision_policy import OperationalContext
from kk.core.fast_commands import run_fast
from kk.core.literal_intents import literal_intent
from kk.core.state import StateMachine
from kk.core.task import AgentTask, CancellationToken
from kk.permissions.policy import PermissionLevel
from kk.tools.registry import Tool, ToolRegistry


def registry():
    policy = Mock()
    memory = Mock()
    memory.path = Path('data/memory.db')
    return ToolRegistry(policy, memory, logging.getLogger('tools_repair'))


def add(tools, name, handler, properties=None, permission=PermissionLevel.ACTION):
    tools.register(Tool(name, name, {'properties': properties or {}}, permission, handler))


class TargetAndLiteralRepairTests(TestCase):
    def test_app_destination_is_literal_input_not_search(self):
        tools = registry()
        opened = Mock(return_value={'verified':True,'hwnd':42,'process_id':7})
        typed = Mock(return_value={'verified':True,'typed':True,'submitted':False,'hwnd':42})
        add(tools, 'open_application', opened)
        add(tools, 'text_entry', typed)
        request = 'В Блокноте напиши: Я открыла Visual Studio Code'
        task = AgentTask(request)
        task.cancellation_token.user_request = request
        context = tools.cancellation.set(task.cancellation_token)
        # The launcher is mocked, so its matching foreground window must be
        # mocked too. Otherwise the real Codex foreground correctly blocks input.
        try:
            with patch('kk.tools.windows.native.foreground_window', return_value={
                    'hwnd':42, 'process_id':7, 'process':'notepad.exe',
                    'executable':'C:/Windows/notepad.exe'}) as foreground:
                answer = run_fast(request, task, tools, StateMachine())
                foreground.assert_called_once_with()
        finally:
            tools.cancellation.reset(context)
        self.assertEqual(answer, 'Написала.')
        opened.assert_called_once_with(name='Блокноте')
        typed.assert_called_once_with(text='Я открыла Visual Studio Code', submit=False)
        self.assertEqual(tools.desktop_context.hwnd,42)

    def test_explicit_search_stays_with_search_route(self):
        self.assertIsNone(literal_intent('В Яндекс Музыке открой поиск и напиши Ojobudo'))

    def test_terminal_payload_is_not_corrected_or_executed(self):
        self.assertEqual(literal_intent('Открой PowerShell и напиши ifconfig'),
                         {'app':'PowerShell','text':'ifconfig','submit':False})
        self.assertEqual(literal_intent('Открой PowerShell и напиши: ifconfig')['text'], 'ifconfig')

    def test_literal_guard_applies_to_destination_form(self):
        tools = registry()
        typed = Mock()
        add(tools,'text_entry',typed)
        token = CancellationToken()
        token.user_request = 'В Блокноте напиши: Я открыла Visual Studio Code'
        tools.cancellation.set(token)
        result = tools.execute('text_entry',{'text':'Я открыла VS Code','submit':False})
        self.assertFalse(result.ok)
        self.assertIn('exact',result.error)
        typed.assert_not_called()

    def test_url_then_pronoun_uses_browser_not_previous_music_window(self):
        tools = registry()
        tools.desktop_context = OperationalContext('Яндекс Музыка',99,monotonic(),9)
        add(tools,'open_url',lambda **_: {'verified':True,'hwnd':42,'browser':'C:/Apps/Firefox/firefox.exe',
            'window':{'hwnd':42,'process':'firefox.exe','process_id':7,'monitor_index':2}})
        maximize = Mock(return_value={'verified':True,'hwnd':42})
        add(tools,'maximize_window',maximize)
        tools.execute('open_url',{'url':'https://www.youtube.com'})
        answer = run_fast('Разверни его',AgentTask('Разверни его'),tools,StateMachine())
        self.assertEqual(answer,'Развернула.')
        maximize.assert_called_once_with(hwnd=42)
        self.assertEqual(tools.desktop_context.process_id,7)
        self.assertEqual(tools.desktop_context.browser,'firefox')

    def test_explicit_application_overrides_recent_music(self):
        tools = registry()
        tools.desktop_context = OperationalContext('Яндекс Музыка',99,monotonic(),9)
        add(tools,'open_application',lambda name: {'verified':True,'hwnd':42,'process_id':7})
        move = Mock(return_value={'after':{'monitor_index':2}})
        add(tools,'move_window_handle_to_monitor',move)
        run_fast('Перенеси Firefox на второй',AgentTask('test'),tools,StateMachine())
        move.assert_called_once_with(hwnd=42,monitor_index=2)

    def test_move_preserves_pid_and_remembers_observed_window(self):
        tools=registry()
        tools.desktop_context=OperationalContext('Firefox',42,monotonic(),7,last_monitor=1)
        after={'hwnd':42,'process_id':7,'monitor_index':2,'bounds':[1920,0,2920,700]}
        add(tools,'move_window_handle_to_monitor',lambda **_: {'after':after})
        run_fast('Перенеси его на второй',AgentTask('test'),tools,StateMachine())
        self.assertEqual(tools.desktop_context.hwnd,42)
        self.assertEqual(tools.desktop_context.process_id,7)
        self.assertEqual(tools.desktop_context.last_monitor,2)
        self.assertEqual(tools.desktop_context.last_window,after)

    def test_compound_opens_browser_then_exact_page(self):
        tools=registry()
        opened=Mock(return_value={'verified':True,'hwnd':42,'process_id':7})
        url=Mock(return_value={'verified':True,'hwnd':42,'browser':'firefox.exe','process_id':7})
        add(tools,'open_application',opened)
        add(tools,'open_url',url)
        run_fast('Открой Firefox, открой YouTube',AgentTask('test'),tools,StateMachine())
        opened.assert_called_once_with(name='Firefox')
        url.assert_called_once_with(url='https://www.youtube.com',browser='Firefox')

    def test_questions_do_not_execute_aliases(self):
        tools=registry()
        for request in ('Расскажи про ChatGPT простыми словами','Что такое Firefox?', 'Зачем нужна музыка?'):
            self.assertIsNone(run_fast(request,AgentTask(request),tools,StateMachine()))
        tools.memory.record_action.assert_not_called()

    def test_reused_explicit_remembered_hwnd_is_rejected(self):
        from kk.tools.windows.window_control import WindowController
        tools=registry()
        tools.desktop_context=OperationalContext('Firefox',42,monotonic(),7)
        controller=WindowController(tools)
        with patch('kk.tools.windows.window_control.u.IsWindow',return_value=True), patch(
            'kk.tools.windows.window_control.window_metadata',return_value={'hwnd':42,'process_id':999,'title':'Other'}):
            with self.assertRaisesRegex(LookupError,'replaced'):
                controller.target(42)

    def test_verification_error_after_typing_does_not_escape_or_retype(self):
        from kk.tools.windows.text_entry import TextEntry
        uia=Mock()
        uia.focused.side_effect=[(Mock(),{'control_type':'EditControl','value':'old'}),OSError('UIA unavailable')]
        entry=TextEntry(registry(),uia)
        with patch('kk.tools.windows.text_entry.foreground_window',return_value={'hwnd':42,'process_id':7}), patch(
            'kk.tools.windows.text_entry.type_text') as typed:
            result=entry.enter('new')
        self.assertFalse(result['verified'])
        self.assertTrue(result['typed'])
        self.assertFalse(result['submitted'])
        typed.assert_called_once()

    def test_typing_stops_if_target_loses_focus(self):
        from kk.tools.windows.text_entry import TextEntry
        uia=Mock()
        uia.focused.return_value=(Mock(),{'control_type':'EditControl','value':'old'})
        entry=TextEntry(registry(),uia)
        before={'hwnd':42,'process_id':7}
        with patch('kk.tools.windows.text_entry.foreground_window',side_effect=[before,before,{'hwnd':99,'process_id':9}]), patch(
            'kk.tools.windows.text_entry.type_text',side_effect=lambda text,check: check()) as typed:
            result=entry.enter('new')
        self.assertFalse(result['verified'])
        self.assertTrue(result['typed'])
        self.assertFalse(result['submitted'])


class MediaRepairTests(TestCase):
    def check_unrelated(self, session):
        from kk.tools.windows.companion_tools import register_companion_tools
        tools=registry()
        tools.decision_policy=SimpleNamespace(preferences={'music_app':'Яндекс Музыка'})
        opened=Mock(return_value={'verified':True,'hwnd':42})
        add(tools,'open_application',opened)
        media=Mock()
        media.call.return_value=[session]
        with patch('kk.tools.windows.companion_tools.MediaController',return_value=media):
            register_companion_tools(tools)
        with patch('kk.tools.windows.companion_tools.monotonic',side_effect=[0,4]):
            result=tools.execute('play_music')
        self.assertTrue(result.ok)
        self.assertFalse(result.data['verified'])
        opened.assert_called_once_with(name='Яндекс Музыка')
        self.assertFalse(any(call.args[0]=='resume' for call in media.call.call_args_list))

    def test_other_playing_music_app_is_not_requested_yandex(self):
        self.check_unrelated({'session_id':'Spotify.exe','playing':True})

    def test_target_audio_peak_is_not_transport_playback(self):
        self.check_unrelated({'session_id':'audio:42','process':'YandexMusic.exe','peak':.1,'muted':False})


class ActionBoundaryRepairTests(TestCase):
    def test_candidate_delays_action_until_released_but_allows_reads(self):
        tools=registry()
        token=CancellationToken()
        token.interruption_pending=Event()
        token.interruption_pending.set()
        token.user_request='open test'
        token.deadline=monotonic()+2
        started=Event()
        finished=Event()
        acted=Event()
        add(tools,'mutation',lambda: acted.set())
        add(tools,'observation',lambda: 'observed',permission=PermissionLevel.READ)
        def run():
            tools.cancellation.set(token)
            started.set()
            tools.execute('mutation')
            finished.set()
        thread=Thread(target=run)
        thread.start()
        self.assertTrue(started.wait(1))
        self.assertFalse(acted.wait(.1))
        tools.cancellation.set(token)
        self.assertEqual(tools.execute('observation').data,'observed')
        token.interruption_pending.clear()
        self.assertTrue(finished.wait(1))
        thread.join()
        self.assertTrue(acted.is_set())

    def test_candidate_cannot_hide_action_deadline(self):
        tools=registry()
        token=CancellationToken()
        token.interruption_pending=Event()
        token.interruption_pending.set()
        token.user_request='open test'
        token.deadline=monotonic()+.05
        tools.cancellation.set(token)
        handler=Mock()
        add(tools,'mutation',handler)
        result=tools.execute('mutation')
        self.assertFalse(result.ok)
        self.assertIn('TimeoutError',result.error)
        handler.assert_not_called()


class MediaCompletionRepairTests(TestCase):
    def task(self, session):
        task=AgentTask('Включи музыку')
        task.entities={'app':'Яндекс Музыка'}
        task.actions=[
            {'tool':'play_music','arguments':{},'result':{'ok':True,'data':{
                'verified':False,'target_application':'Яндекс Музыка'}}},
            {'tool':'get_current_media','arguments':{},'result':{'ok':True,'data':session}}]
        return task

    def test_fallback_cannot_verify_other_playing_music_player(self):
        from kk.core.completion import review
        provider=Mock()
        complete,reason=review(provider,'test',self.task({'session_id':'Spotify.exe','playing':True}),'Включила')
        self.assertFalse(complete)
        self.assertIn('целевого',reason)
        provider.chat.assert_not_called()
        provider.chat_stream.assert_not_called()

    def test_matching_target_transport_reaches_semantic_review(self):
        from kk.core.completion import review
        from kk.providers.base import LLMResponse
        provider=SimpleNamespace(chat=Mock(return_value=LLMResponse(
            '{"complete":true,"reason":"Играет Яндекс Музыка","evidence":"target transport playing"}')))
        complete,_=review(provider,'test',self.task({'session_id':'YandexMusic.exe','playing':True}),'Включила')
        self.assertTrue(complete)
        provider.chat.assert_called_once()

    def test_core_audio_claim_is_not_transport_verification(self):
        from kk.core.completion import review
        provider=Mock()
        complete,_=review(provider,'test',self.task({'session_id':'audio:42','process':'YandexMusic.exe',
            'playing':True,'verified':True}),'Включила')
        self.assertFalse(complete)
        provider.chat.assert_not_called()


class DesktopReceiptRepairTests(TestCase):
    def test_verified_launch_uses_natural_receipt_and_actual_telegram_client(self):
        from kk.core.desktop_commands import run_desktop
        from kk.tools.registry import ToolResult
        for request, executable, expected in (
            ('Открой Яндекс Музыку', 'C:/Apps/YandexMusic/Яндекс Музыка.exe', 'Открыла Яндекс Музыку.'),
            ('Открой Firefox', 'C:/Apps/Firefox/firefox.exe', 'Открыла Firefox.'),
            ('Открой DS', 'C:/Apps/Discord/Discord.exe', 'Открыла Discord.'),
            ('Открой TG', 'C:/AyuGram/AyuGram.exe', 'Открыла AyuGram.'),
            ('Открой Telegram', 'C:/Apps/Telegram/Telegram.exe', 'Открыла Telegram.'),
            ('Открой SuperApp Pro', 'C:/Apps/SuperApp.exe', 'Открыла SuperApp Pro.'),
        ):
            with self.subTest(request=request):
                tools=SimpleNamespace(personal_intents=None,execute=Mock(return_value=ToolResult(True,
                    {'verified':True,'hwnd':42,'process_id':7,'executable':executable})))
                answer=run_desktop(request,AgentTask(request),tools,StateMachine(),OperationalContext())
                self.assertEqual(answer,expected)


class NativeTransportCompletionRepairTests(TestCase):
    def task(self, request='Приостанови музыку', operation='pause', status=5):
        task=AgentTask(request)
        task.entities={'app':'Firefox','hwnd':88}
        task.observations=[{'ok':True,'data':{'foreground':{'process':'firefox.exe','hwnd':88}}}]
        task.actions=[{'tool':operation+'_media','arguments':{},'result':{'ok':True,'data':{
            'verified':True,'session_id':'ru.yandex.desktop.music','requested':operation,
            'status':status,'playing':status==4}}}]
        return task

    def test_native_pause_is_complete_even_with_foreground_browser(self):
        from kk.core.completion import review
        provider=Mock()
        complete,_=review(provider,'test',self.task(),'Музыка приостановлена.')
        self.assertTrue(complete)
        provider.chat_stream.assert_not_called()
        provider.chat.assert_not_called()

    def test_native_resume_is_complete_without_focus_change_or_llm_review(self):
        from kk.core.completion import review
        provider=Mock()
        complete,_=review(provider,'test',self.task('Возобнови воспроизведение','resume',4),'Включила.')
        self.assertTrue(complete)
        provider.chat_stream.assert_not_called()

    def test_wrong_selected_session_is_rejected(self):
        from kk.core.completion import review
        task=self.task()
        task.actions[0]['arguments']['session_id']='Spotify.exe'
        self.assertFalse(review(Mock(),'test',task,'Готово')[0])

    def test_acceptance_without_status_verification_is_rejected(self):
        from kk.core.completion import review
        task=self.task()
        task.actions[0]['result']['data']['verified']=False
        self.assertFalse(review(Mock(),'test',task,'Готово')[0])

    def test_later_window_focus_does_not_invalidate_session_pause(self):
        from kk.core.completion import review
        task=self.task()
        task.actions.append({'tool':'focus_window','arguments':{'hwnd':88},'result':{'ok':True,'data':{'verified':True}}})
        self.assertTrue(review(Mock(),'test',task,'Готово')[0])

    def test_compound_request_still_requires_full_goal_review(self):
        from kk.core.completion import review
        from kk.providers.base import LLMResponse
        task=self.task('Приостанови музыку и открой Firefox')
        provider=SimpleNamespace(chat=Mock(return_value=LLMResponse(
            '{"complete":false,"reason":"Firefox не открыт","evidence":""}')))
        self.assertFalse(review(provider,'test',task,'Готово')[0])
        provider.chat.assert_called_once()
