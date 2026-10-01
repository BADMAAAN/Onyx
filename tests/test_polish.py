import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from time import monotonic
from unittest import TestCase
from unittest.mock import Mock, patch
import numpy as np
from kk.core.desktop_commands import parse_plan, DesktopContext, run_desktop
from kk.core.state import StateMachine, AgentState
from kk.core.task import AgentTask
from kk.core.fast_commands import run_fast
from kk.core.orchestrator import Orchestrator, requests_action
from kk.core.task_batch import TaskBatch
from kk.tools.registry import Tool, ToolRegistry, ToolResult
from kk.permissions.policy import PermissionLevel as P, PermissionPolicy
from kk.memory.database import MemoryDatabase
from kk.ui.presence import presence
from kk.voice.conversation import ConversationSession


class PolishTests(TestCase):
    def registry(self):
        folder=TemporaryDirectory(); self.addCleanup(folder.cleanup)
        db=MemoryDatabase(Path(folder.name)/'m.db'); self.addCleanup(db.close)
        return ToolRegistry(PermissionPolicy(P.DANGEROUS,Path(folder.name)/'STOP'),db,logging.getLogger('test'))
    def test_visibility_voice_variants(self):
        registry=self.registry(); state=StateMachine()
        registry.register(Tool('set_orb_visibility','',{'properties':{'visible':{}},'required':['visible']},P.ACTION,state.set_orb_visible))
        for visible,phrases in [(False,['сделай себя невидимой','скройся','убери себя','спрячь кружок','убери orb']),
                               (True,['покажись','сделай себя видимой','верни кружок','покажи orb'])]:
            for phrase in phrases:
                answer=run_fast('Кей Кей, '+phrase,AgentTask(phrase),registry,state)
                self.assertIsNotNone(answer); self.assertEqual(state.orb_visible,visible)
    def test_hidden_keeps_voice_state(self):
        state=StateMachine(); state.set(AgentState.LISTENING); state.conversation_active=True
        state.set_orb_visible(False)
        self.assertEqual(state.state,AgentState.LISTENING); self.assertTrue(state.conversation_active)
    def test_hidden_edge_active_only(self):
        self.assertEqual(presence(False,True,False),{'orb':False,'edge':True})
        self.assertEqual(presence(False,False,False),{'orb':False,'edge':False})
    def test_fullscreen_hides_without_waking(self):
        self.assertEqual(presence(True,False,True),{'orb':False,'edge':False})
        self.assertEqual(presence(True,True,True),{'orb':False,'edge':True})
    def test_fullscreen_config(self):
        self.assertEqual(presence(True,True,True,False),{'orb':True,'edge':False})
        self.assertFalse(presence(False,True,True,True,False)['edge'])
    def test_normal_maximize_and_explicit_fullscreen(self):
        for phrase in ['сделай его на полный экран','сделай окно на полный экран','разверни','на весь экран']:
            self.assertEqual(parse_plan(phrase)[0]['operation'],'maximize')
        self.assertEqual(parse_plan('включи полноэкранный режим')[0]['operation'],'fullscreen')
    def test_omitted_move_referent(self):
        context=DesktopContext('AyuGram',123,monotonic())
        plan=parse_plan('Перенеси на второй',context)[0]
        self.assertEqual(plan['monitor'],2); self.assertEqual(plan['app'],'AyuGram')
    def test_tg_maximize_uses_same_hwnd(self):
        tools=SimpleNamespace(personal_intents=None,execute=Mock())
        tools.execute.side_effect=[ToolResult(True,{'hwnd':321,'verified':True}),ToolResult(True,{'hwnd':321,'verified':True})]
        context=DesktopContext(); state=StateMachine()
        run_desktop('Открой TG',AgentTask('TG'),tools,state,context)
        run_desktop('Сделай его на полный экран',AgentTask('max'),tools,state,context)
        self.assertEqual(tools.execute.call_args.args,('maximize_window',{'hwnd':321}))
    def test_new_open_updates_referent(self):
        tools=SimpleNamespace(personal_intents=None,execute=Mock(return_value=ToolResult(True,{'hwnd':99,'verified':True})))
        context=DesktopContext('AyuGram',321,monotonic())
        run_desktop('Теперь открой Discord',AgentTask('open'),tools,StateMachine(),context)
        self.assertEqual((context.app,context.hwnd),('Discord',99))
    def test_mixed_monitor_batch(self):
        plan=parse_plan('Открой Firefox на первом, Discord на втором, а TG разверни')
        self.assertEqual([s['kind'] for s in plan],['open','open','window'])
        self.assertEqual(plan[-1]['app'],'telegram')
    def test_referent_dependency_skips_after_failed_open(self):
        plan=parse_plan('Запусти Discord, перенеси его на второй экран и разверни')
        batch=TaskBatch.from_plan('test',plan)
        self.assertEqual(batch.tasks[1].dependencies,[batch.tasks[0].id])
        self.assertEqual(batch.tasks[2].dependencies,[batch.tasks[1].id])
    def test_composites_route_to_agent(self):
        for phrase in ['Открой Firefox, зайди на YouTube и найди видео про космос',
                       'Открой Яндекс Музыку и включи последний добавленный трек',
                       'Запусти браузер и зайди на YouTube','Хочу YouTube']:
            self.assertIsNone(parse_plan(phrase),phrase)
            self.assertTrue(requests_action(phrase),phrase)
    def test_full_catalog_without_mouse_keyword(self):
        registry=self.registry()
        for name in ['move_mouse','press_hotkey','click_ui_element','run_terminal','get_media_sessions']:
            registry.register(Tool(name,'',{'properties':{}},P.ACTION,lambda:None))
        for request in ['включи последний добавленный трек','найди папку Downloads']:
            self.assertEqual(len(registry.schemas_for(request)),5)
    def test_full_no_confirmation(self):
        registry=self.registry(); registry.policy.confirmer=Mock()
        registry.register(Tool('test','',{'properties':{}},P.DANGEROUS,lambda:True))
        self.assertTrue(registry.execute('test').ok); registry.policy.confirmer.assert_not_called()
    def test_continuous_session(self):
        state=StateMachine(); session=ConversationSession(25,state)
        session.touch(0); self.assertTrue(state.conversation_active)
        session.touch(20); self.assertFalse(session.expire(now=30))
        self.assertTrue(session.expire(now=46)); self.assertFalse(state.conversation_active)
    def test_session_farewell_context(self):
        session=ConversationSession(); self.assertFalse(session.closing('спасибо'))
        session.touch(); self.assertTrue(session.closing('спасибо'))
        self.assertFalse(session.closing('спасибо, а теперь открой TG'))
        one=session.farewell(); self.assertEqual(session.phase,'closing'); self.assertTrue(session.active)
        self.assertNotEqual(one,session.farewell())
    def test_session_phases(self):
        s=ConversationSession(); s.touch()
        for phase in [AgentState.LISTENING,AgentState.THINKING,AgentState.ACTING,AgentState.SPEAKING]:
            s.transition(phase); self.assertEqual(s.phase,phase.value.casefold())
    def test_pronunciation_and_ramps(self):
        from kk.voice.prosody import prepare,ramp
        self.assertEqual(prepare('**Firefox** — [YouTube](https://youtube.com).'),'Файерфокс, Ютуб.')
        wave=ramp(np.ones(1000),48000)
        self.assertEqual(wave[0],0); self.assertEqual(wave[-1],0); self.assertEqual(wave[500],1)
    def test_female_persona(self):
        from kk.core.persona import Persona
        self.assertEqual(Persona().render('Открыл. Перенёс. Запомнил.'),'Открыла. Перенесла. Запомнила.')
    def test_stale_referent_rejected(self):
        from kk.tools.windows.window_control import WindowController
        registry=SimpleNamespace(desktop_context=DesktopContext('TG',123,monotonic(),4))
        with patch('kk.tools.windows.window_control.u') as u, patch('kk.tools.windows.window_control.window_metadata',return_value={'process_id':5}):
            u.IsWindow.return_value=True
            with self.assertRaises(LookupError): WindowController(registry).target()
    def test_maximize_native_readback(self):
        from kk.tools.windows.window_control import WindowController
        registry=SimpleNamespace(check_cancel=lambda:None,desktop_context=DesktopContext('TG',123,monotonic()))
        controller=WindowController(registry)
        controller.state=Mock(side_effect=[dict(hwnd=123,maximized=False),dict(hwnd=123,title='TG',maximized=True,minimized=False)])
        with patch('kk.tools.windows.window_control.u') as u:
            self.assertTrue(controller.change('maximize')['verified'])
            u.ShowWindowAsync.assert_called_once_with(123,3)

    def agent_scenario(self,request,steps,answer):
        from kk.providers.base import LLMResponse, ToolCall
        registry=self.registry(); state=StateMachine(); observations=[]
        def observe():
            observations.append(True)
            elements=[{'name':'search','control_type':'Edit'}]
            if 'последний добавленный трек' in request:
                elements.append({'name':'По дате добавления — сначала новые','control_type':'ButtonControl'})
            return {'foreground':{'title':'Task application'},'ui_elements':elements}
        registry.register(Tool('observe_context','',{'properties':{}},P.READ,observe))
        for name in set(s[0] for s in steps):
            registry.register(Tool(name,'',{'properties':{'name':{},'url':{},'text':{}}},P.READ if name=='get_current_media' else P.ACTION,lambda **kw: {'verified':True,'playing':True,'title':'Last saved track'}))
        responses=[LLMResponse(tool_calls=[ToolCall(str(i),name,args)]) for i,(name,args) in enumerate(steps)]
        responses.append(LLMResponse(content=answer))
        provider=SimpleNamespace(chat=Mock(side_effect=responses))
        agent=Orchestrator(provider,'fake',registry,registry.memory,logging.getLogger('test'),state,12)
        # All app actions are fakes; the permission guard must inspect the same
        # fake foreground, not whichever real window the developer is using.
        with patch('kk.tools.windows.native.foreground_window',return_value={'process':'test.exe','executable':'test.exe'}):
            result=agent.respond(request)
        self.assertEqual(result,answer)
        self.assertEqual([a['tool'] for a in agent.last_task.actions],[s[0] for s in steps])
        self.assertGreaterEqual(len(observations),len(steps))
        return provider
    def test_firefox_general_observe_act_verify(self):
        self.agent_scenario('Открой Firefox, зайди на YouTube и найди видео про космос',
            [('open_application',{'name':'Firefox'}),('open_url',{'url':'https://youtube.com'}),
             ('set_ui_value',{'name':'Search','text':'космос'}),('click_ui_element',{'name':'Search'})],
             'Нашла видео про космос на YouTube.')
    def test_yandex_generic_semantic_ui_loop(self):
        self.agent_scenario('Открой Яндекс Музыку и включи последний добавленный трек',
            [('open_application',{'name':'Yandex Music'}),('click_ui_element',{'name':'Коллекция'}),
             ('click_ui_element',{'name':'По дате добавления'}),('click_ui_element',{'name':'Last saved track'}),
             ('get_current_media',{})], 'Последний добавленный трек играет.')

class MediaVolumeTests(TestCase):
    def test_relative_volume_changes_restore_baseline(self):
        from kk.voice.audio_focus import AudioFocusTransaction
        row=dict(pid=99,active=True,muted=False,peak=.5,volume=.8)
        backend=SimpleNamespace(sessions=lambda:{'music':dict(row)},set=lambda s,v:row.update(volume=v))
        with TemporaryDirectory() as folder:
            transaction=AudioFocusTransaction(backend,Path(folder)/'duck.json',fade_ms=0,own_pid=1)
            transaction.duck(); self.assertAlmostEqual(row['volume'],.2)
            result=transaction.adjust('music',-10); self.assertTrue(result['verified'])
            self.assertAlmostEqual(row['volume'],.175)
            transaction.restore(); self.assertAlmostEqual(row['volume'],.7)

class VoicePolishTests(TestCase):
    def streaming_loop(self):
        from tests.test_voice_ux import CommittedVoiceTests
        return CommittedVoiceTests.streaming_loop(self)
    def test_wake_only_no_brain_then_followup(self):
        from tests.test_voice_ux import heard
        from time import sleep
        loop,stt,frames,entered,release=self.streaming_loop()
        stt.transcribe.return_value=heard('Кей Кей')
        stt.transcribe_final.side_effect=lambda *args: heard('Кей Кей',True)
        for _ in range(12): frames.put(bytes([1,0])*480)
        sleep(.4)
        self.assertTrue(loop.commands.empty()); self.assertTrue(loop.conversation.active)
        loop.agent.ask.assert_not_called()
        stt.transcribe.return_value=heard('открой Discord')
        stt.transcribe_final.side_effect=lambda *args: heard('открой Discord',True)
        for _ in range(12): frames.put(bytes([1,0])*480)
        self.assertEqual(loop.commands.get(timeout=2)[0],'открой Discord')
    def test_closing_restores_after_speech_not_emergency_pause(self):
        from tests.test_voice_ux import heard
        from time import sleep
        loop,stt,frames,entered,release=self.streaming_loop()
        loop.speech.done.is_set.return_value=False
        loop.speech.begin.return_value=__import__('kk.core.task',fromlist=['CancellationToken']).CancellationToken()
        loop.conversation.touch()
        stt.transcribe.return_value=heard('спасибо')
        stt.transcribe_final.side_effect=lambda *args: heard('спасибо',True)
        for _ in range(12): frames.put(bytes([1,0])*480)
        sleep(.4)
        self.assertEqual(loop.conversation.phase,'closing'); self.assertFalse(loop.paused)
        loop.audio_focus.release.assert_not_called()
        loop.speech.done.is_set.return_value=True
        sleep(.15)
        self.assertFalse(loop.conversation.active); loop.audio_focus.release.assert_called()
    def test_natural_request_not_rejected_by_fast_parser(self):
        from tests.test_voice_ux import heard
        from kk.voice.recognition import suspicion
        result=heard('Открой Firefox, зайди на YouTube и найди видео про космос',True)
        self.assertEqual(suspicion(result),[])

class CompletionTests(TestCase):
    def test_review_missing_evidence_never_claims_success(self):
        from kk.core.completion import review
        from kk.providers.base import LLMResponse
        task=AgentTask('найди видео')
        task.actions=[{'tool':'type_text','arguments':{'text':'url'},'result':{'ok':True,'data':{'typed':True}}}]
        provider=SimpleNamespace(chat=Mock(return_value=LLMResponse(content='{"complete":false,"reason":"URL только введён","evidence":""}')))
        self.assertFalse(review(provider,'test',task,'Готово')[0])
        prompt=provider.chat.call_args.args[1]
        self.assertIn('type_text',prompt[-1]['content'])
    def test_review_invalid_json_is_not_confirmation(self):
        from kk.core.completion import review
        from kk.providers.base import LLMResponse
        provider=SimpleNamespace(chat=lambda *args:LLMResponse(content='Конечно!'))
        self.assertFalse(review(provider,'test',AgentTask('test'),'Готово')[0])
