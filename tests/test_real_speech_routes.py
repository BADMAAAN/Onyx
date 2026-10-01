"""Regressions for newly reported phrases at production routing boundaries."""
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from kk.core.desktop_commands import parse_plan
from kk.core.fast_commands import route_fast
from kk.core.orchestrator import Orchestrator, _fragment_answer
from kk.core.state import StateMachine
from kk.core.task import AgentTask, CancellationToken, TaskStatus
from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.providers.base import LLMResponse
from kk.tools.registry import ToolRegistry, ToolResult
from kk.voice.stt_faster_whisper import Transcription
from kk.voice.wakeword import STTWakeWordProvider


class RealSpeechRouteTests(TestCase):
    def test_close_word_order_and_politeness_share_canonical_target(self):
        expected = [{'kind':'window', 'operation':'close', 'app':'VS Code'}]
        for text in ('Закрой VS Code', 'VS Code закрой', 'закрой, пожалуйста, ви эс код',
                     'Оникс, VS Code, закрой', 'закрой, VS Code'):
            with self.subTest(text=text): self.assertEqual(parse_plan(text), expected)

    def test_window_comma_does_not_merge_action_clauses_or_rewrite_names(self):
        from kk.core.window_intents import window_intent
        for text in ('открой VS Code, закрой','не закрывай VS Code, закрой',
                     'VS Code, Obsidian, закрой','закрой, «Test, Name»'):
            with self.subTest(text=text):self.assertIsNone(window_intent(text))

    def test_clear_semantic_request_is_not_rejected_for_fast_parser_miss(self):
        from kk.voice.recognition import recognize_final
        text='Оникс, не закрывай VS Code, открой папку Onyx на рабочем столе'
        self.assertIsNone(parse_plan(text))
        final=Transcription(text,'ru',1,5,.1,confidence=.95,intent_confidence=.95,
                            raw_transcript=text,normalized_transcript=text,committed=True)
        stt=SimpleNamespace(decode_full=Mock(return_value=final))
        self.assertIs(recognize_final(stt,object()),final)
        self.assertTrue(final.accepted)
        self.assertEqual(final.text,text)
        self.assertEqual(stt.decode_full.call_count,1)

    def test_politeness_is_removed_only_from_entity_position(self):
        self.assertEqual(parse_plan('открой мне Обсидиан'), parse_plan('Обсидиан открой'))
        from kk.core.literal_intents import literal_intent
        self.assertEqual(literal_intent('Оникс, напиши: «Мне нужен Onyx, не закрывай VS Code!»')['text'],
                         'Мне нужен Onyx, не закрывай VS Code!')

    def test_quoted_negative_payload_is_data_while_unquoted_cancellation_falls_back(self):
        from kk.core.literal_intents import literal_intent
        for payload in ('не выполняй ifconfig','не отправляй сообщение','ничего не пиши'):
            self.assertEqual(literal_intent('напиши: «'+payload+'»'),
                             {'app':None,'text':payload,'submit':False})
            self.assertIsNone(literal_intent('напиши: '+payload))
        tools=SimpleNamespace(_tools={'text_entry':object()},execute=Mock(return_value=ToolResult(True,{'verified':True})))
        text='напиши: «не выполняй ifconfig»'
        route_fast(text,AgentTask(text),tools,StateMachine())
        tools.execute.assert_called_once_with('text_entry',{'text':'не выполняй ifconfig','submit':False})

    def test_explicit_target_type_prevents_name_collision(self):
        for phrase, name, kind in [('открой папку Onyx на рабочем столе','Onyx','folder'),
                                   ('открой файл Discord.png','Discord.png','file'),
                                   ('открой документ VS Code.txt','VS Code.txt','document')]:
            plan = parse_plan(phrase)
            self.assertEqual((plan[0]['name'],plan[0]['type']), (name,kind))
            self.assertNotIn('app',plan[0])
        self.assertEqual(parse_plan('открой папку Onyx на рабочем столе')[0]['location'], 'desktop')

    def test_negation_preserved_without_executing_positive_fragment(self):
        text = 'не закрывай VS Code, открой папку'
        tools = SimpleNamespace(execute=Mock())
        result = route_fast(text, AgentTask(text), tools, StateMachine())
        self.assertEqual(result.status, 'not_handled')
        self.assertIsNone(result.answer)
        tools.execute.assert_not_called()
        self.assertIsNone(parse_plan(text))

    def test_negated_imperative_and_contrast_never_launch_second_app(self):
        for text in ('открой VS Code, а не Visual Studio', 'не закрой VS Code',
                     'не открой Discord', 'открой Firefox, но не закрой VS Code'):
            tools=SimpleNamespace(execute=Mock())
            self.assertIsNone(parse_plan(text))
            self.assertEqual(route_fast(text,AgentTask(text),tools,StateMachine()).status,'not_handled')
            tools.execute.assert_not_called()

    def test_explicit_type_with_leading_location_and_unparsed_type_dont_become_apps(self):
        self.assertEqual(parse_plan('открой на рабочем столе папку Onyx'),
                         parse_plan('открой папку Onyx на рабочем столе'))
        self.assertIsNone(parse_plan('открой в неизвестном месте папку Onyx'))
        self.assertEqual(parse_plan('открой файл «не закрывай.txt»')[0]['name'],'не закрывай.txt')
        self.assertEqual(parse_plan('открой файл «A. B...txt»')[0]['name'],'A. B...txt')

    def test_explicit_self_correction_still_uses_replacement_only(self):
        self.assertEqual(parse_plan('открой VS Code, нет, Discord'),
                         [{'kind':'open','app':'Discord','monitor':None}])

    def test_close_never_launches_the_app_to_close_it(self):
        tools = SimpleNamespace(execute=Mock(return_value=ToolResult(True, {'verified':True,'name':'VS Code'})))
        task = AgentTask('VS Code закрой')
        result = route_fast(task.user_request, task, tools, StateMachine())
        self.assertEqual(result.status, 'handled')
        tools.execute.assert_called_once_with('close_application', {'query':'VS Code','scope':'application'})
        self.assertEqual(result.answer, 'Закрыла VS Code.')

    def test_error_route_is_not_success_or_entity_label(self):
        raw='Диалог несохранённого файла: [WinError 5] C:\\private\\document.txt'
        tools = SimpleNamespace(execute=Mock(return_value=ToolResult(False, error=raw)))
        task=AgentTask('VS Code закрой')
        with self.assertLogs('kk',level='ERROR') as logs:
            result = route_fast(task.user_request, task, tools, StateMachine())
        self.assertEqual(result.status, 'error')
        self.assertEqual(task.status,TaskStatus.FAILED)
        self.assertEqual(result.answer,'VS Code: Не удалось выполнить действие. Подробности — в журнале Onyx.')
        self.assertNotIn('WinError',result.answer)
        self.assertNotIn('C:\\private',result.answer)
        self.assertIn(raw,'\n'.join(logs.output))
        tools.execute.assert_called_once_with('close_application',{'query':'VS Code','scope':'application'})

    def test_recycle_bin_open_is_contextual_and_never_empty(self):
        self.assertEqual(parse_plan('открой мусорку')[0]['type'], 'virtual_shell_item')
        self.assertIsNone(parse_plan('вынеси мусор'))

    def test_wake_prefix_preserves_named_object_and_does_not_trigger_mid_sentence(self):
        detector = STTWakeWordProvider('оникс')
        self.assertEqual(detector.command('Оникс, открой папку Onyx на рабочем столе'), 'открой папку Onyx на рабочем столе')
        for text in ('открой папку Onyx', 'скажи Оникс, привет', 'окей, открой музыку'):
            self.assertFalse(detector.detect_text(text))

    def test_final_suffix_verb_does_not_wait_as_unfinished_command(self):
        from kk.voice.streaming import unfinished
        self.assertFalse(unfinished('Обсидиан открой'))
        self.assertFalse(unfinished('VS Code закрой'))
        self.assertTrue(unfinished('открой'))

    def test_action_noun_echo_rejected_but_deliberate_word_repeat_allowed(self):
        for request,answer in [('открой Яндекс Музыку','Яндекс Музыка'), ('VS Code закрой','VS')]:
            self.assertTrue(_fragment_answer(request,answer))
        self.assertFalse(_fragment_answer('Повтори слово YouTube','YouTube'))
        self.assertFalse(_fragment_answer('открой Obsidian','Открыла.'))

    def test_action_gate_does_not_force_negated_commands_or_word_repetitions(self):
        from kk.core.orchestrator import requests_action
        for text in ('не закрой VS Code','не открой Discord','не надо открыть Visual Studio',
                     'Повтори слово открой','Оникс, скажи слово напиши',"do not close VS Code"):
            self.assertFalse(requests_action(text),text)
        self.assertTrue(requests_action('не закрывай VS Code, открой папку Onyx'))

    def test_fast_target_clarification_keeps_exact_question_and_waiting(self):
        question='Нашла папку «онкис». Открыть её?'
        tools=SimpleNamespace(execute=Mock(return_value=ToolResult(False,{'status':'needs_clarification','question':question},'ambiguous')))
        task=AgentTask('открой папку Onyx на рабочем столе')
        result=route_fast(task.user_request,task,tools,StateMachine())
        self.assertEqual(result.answer,question)
        self.assertEqual(task.status,TaskStatus.WAITING)
        self.assertEqual(task.batch.status,'waiting')
        self.assertEqual(len(task.actions),1)

    def test_low_confidence_clarification_is_a_full_sentence_not_noun_echo(self):
        from kk.voice.recognition import fragment_question
        result = Transcription('VS Code закрой','ru',1,1,.1, accepted=False, rejection_reason='low_confidence')
        self.assertIn('команду целиком', fragment_question(result))
        result.text='что то';result.words=[{'word':'VS','probability':.2}]
        self.assertEqual(fragment_question(result),'Не расслышала реплику целиком. Повтори, пожалуйста.')

    def test_collection_calls_tab_enumeration_and_discloses_partial_coverage(self):
        tools = SimpleNamespace(execute=Mock(return_value=ToolResult(True, {
            'items':[{'title':'Page A','active':True}], 'completeness':'partial',
            'limitations':['доступно только одно окно']})))
        result = route_fast('покажи все вкладки', AgentTask('test'), tools, StateMachine())
        tools.execute.assert_called_once_with('list_browser_tabs',{'scope':'all_windows'})
        self.assertIn('Полный список не подтверждён', result.answer)
        self.assertNotIn('других нет', result.answer)

    def test_obsidian_composition_uses_new_draft_exact_payload_once(self):
        tools = SimpleNamespace(_tools={'text_entry':object()}, execute=Mock(return_value=ToolResult(True,{'verified':True})))
        text = 'Открой мне Обсидиан и напиши: «Onyx, мне нужен ifconfig!»'
        result = route_fast(text, AgentTask(text), tools, StateMachine())
        tools.execute.assert_called_once_with('open_obsidian_draft', {'text':'Onyx, мне нужен ifconfig!'})
        self.assertEqual(result.status,'handled')

    def test_spoken_comma_delimiter_keeps_actual_stt_payload_exact(self):
        from kk.core.literal_intents import literal_intent
        payload='Оникс, мне нужен эвкранфиг'
        text='Оникс, открой обсидиан и напиши, '+payload
        self.assertEqual(literal_intent(text),dict(app='обсидиан',text=payload,submit=False))
        self.assertEqual(literal_intent('в Обсидиан напиши, «Onyx, не выполняй ifconfig!»')['text'],
                         'Onyx, не выполняй ifconfig!')
        tools=SimpleNamespace(_tools={'text_entry':object()},execute=Mock(return_value=ToolResult(True,{'verified':True})))
        result=route_fast(text,AgentTask(text),tools,StateMachine())
        self.assertEqual(result.status,'handled')
        tools.execute.assert_called_once_with('open_obsidian_draft',{'text':payload})

    def test_registry_comma_literal_guard_rejects_payload_repair_before_handler(self):
        from kk.tools.registry import Tool
        with TemporaryDirectory() as directory:
            root=Path(directory);db=MemoryDatabase(root/'memory.db')
            try:
                tools=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,root/'STOP'),db,logging.getLogger('test'))
                handler=Mock(return_value={'verified':True})
                tools.register(Tool('open_obsidian_draft','',{'type':'object','properties':{'text':{'type':'string'}},
                    'required':['text']},PermissionLevel.ACTION,handler))
                token=CancellationToken()
                token.user_request='Оникс, открой обсидиан и напиши, Оникс, мне нужен эвкранфиг'
                binding=tools.cancellation.set(token)
                try:
                    wrong=tools.execute('open_obsidian_draft',{'text':'Onyx, мне нужен ifconfig'})
                    self.assertFalse(wrong.ok)
                    self.assertIn('Literal text must remain exact',wrong.error)
                    handler.assert_not_called()
                    right=tools.execute('open_obsidian_draft',{'text':'Оникс, мне нужен эвкранфиг'})
                    self.assertTrue(right.ok)
                    handler.assert_called_once_with(text='Оникс, мне нужен эвкранфиг')
                finally:tools.cancellation.reset(binding)
            finally:db.close()

    def test_transcript_details_are_diagnostic_only(self):
        for enabled in (False,True):
            token = CancellationToken(diagnostic=enabled)
            token.mark('planner_fallback', final_transcript='Private', request='Private', context={'secret':'value'})
            self.assertEqual('final_transcript' in token.trace[0],enabled)


class RealSpeechOrchestratorTests(TestCase):
    def test_scoped_confirmation_with_real_registry_and_filesystem_revalidation(self):
        from kk.tools.registry import Tool
        from kk.tools.windows.app_resolver import ApplicationResolver, TargetClarificationRequired
        for scenario in ('yes','no','stale','different_session','different_task','ambiguous','unrelated','removed'):
            with self.subTest(scenario=scenario), TemporaryDirectory() as directory:
                root=Path(directory);candidate=root/'Onxy';candidate.mkdir()
                db=MemoryDatabase(root/'memory.db')
                try:
                    tools=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,root/'STOP'),db,logging.getLogger('test'))
                    resolver=ApplicationResolver();side_effects=[]
                    question='Нашла папку Onxy. Открыть её?'
                    def target_action(name,type,location=''):
                        if name=='Onyx':
                            candidates=[{'name':'Onxy','path':str(candidate),'type':'folder'}]
                            if scenario=='ambiguous':candidates.append({'name':'Other','path':str(root/'Other'),'type':'folder'})
                            tools.cancellation.get().target_confirmation_required=dict(name=name,type=type,location=str(root),
                                candidates=candidates,question=question)
                            raise TargetClarificationRequired(question)
                        # Real filesystem resolution is repeated only after the
                        # current answer authorizes it; no Windows UI is touched.
                        resolved=resolver.resolve_target(name,type,location,refresh=True)
                        if not resolved['found']: raise FileNotFoundError(name)
                        target=resolved['items'][0]
                        marker=Path(target['path'])/'confirmed.test'
                        marker.write_text('confirmed',encoding='utf-8')
                        side_effects.append(str(marker))
                        return {'verified':True,'target':target,'evidence':'offline filesystem fixture'}
                    tools.register(Tool('open_desktop_target','',{'type':'object','properties':{
                        'name':{'type':'string'},'type':{'type':'string'},'location':{'type':'string'}},
                        'required':['name','type']},PermissionLevel.ACTION,target_action))
                    provider=SimpleNamespace(chat=Mock(return_value=LLMResponse('Хорошо.')))
                    agent=Orchestrator(provider,'test',tools,db,logging.getLogger('test'),StateMachine())
                    first=CancellationToken(session_id='fixture-session')
                    self.assertEqual(agent.respond('открой папку Onyx',cancellation=first),question)
                    previous_id=agent.last_task.id
                    self.assertEqual(agent.last_task.status,TaskStatus.WAITING)
                    if scenario=='yes':agent.pending_skill={'name':'stale unrelated proposal'}
                    if scenario=='stale':agent.pending_target_confirmation['expires']=0
                    if scenario=='different_task':agent.last_task=AgentTask('different task')
                    if scenario=='removed':candidate.rmdir()
                    if scenario=='unrelated':
                        agent.respond('Как дела?',cancellation=CancellationToken(session_id='fixture-session'))
                    reply='нет' if scenario=='no' else 'да'
                    second=CancellationToken(session_id='other' if scenario=='different_session' else 'fixture-session')
                    answer=agent.respond(reply,cancellation=second)
                    self.assertEqual(agent.last_task.final_transcript,reply)
                    self.assertEqual(agent.last_task.user_request,reply)
                    if scenario=='yes':
                        self.assertTrue((candidate/'confirmed.test').exists())
                        self.assertEqual(len(side_effects),1)
                        self.assertEqual(agent.last_task.metrics['confirmed_intent']['source_task_id'],previous_id)
                        self.assertEqual(agent.last_task.actions[0]['arguments']['name'],str(candidate))
                        self.assertIn('Onxy',answer)
                        self.assertIsNone(agent.pending_skill)
                        agent.respond('да', cancellation=CancellationToken(session_id='fixture-session'))
                        self.assertEqual(len(side_effects), 1)
                        self.assertEqual(agent.last_task.actions, [])
                    else:
                        self.assertEqual(side_effects,[])
                    if scenario=='removed':self.assertEqual(agent.last_task.status,TaskStatus.FAILED)
                    if scenario=='ambiguous':
                        self.assertEqual(answer,question)
                        self.assertEqual(agent.last_task.status,TaskStatus.WAITING)
                        self.assertIsNotNone(agent.pending_target_confirmation)
                        provider.chat.assert_not_called()
                    else:self.assertIsNone(agent.pending_target_confirmation)
                finally:db.close()

    def test_planner_cannot_substitute_candidate_after_required_clarification(self):
        from kk.providers.base import ToolCall
        from kk.tools.registry import Tool
        from kk.tools.windows.app_resolver import TargetClarificationRequired
        with TemporaryDirectory() as directory:
            db=MemoryDatabase(Path(directory)/'memory.db')
            try:
                tools=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(directory)/'STOP'),db,logging.getLogger('test'))
                question='Точного имени Onyx нет. Открыть папку «онкис»?'
                calls=[]
                def open_target(name,type,location=''):
                    calls.append(name)
                    if name=='Onyx': raise TargetClarificationRequired(question)
                    raise AssertionError('Near candidate must not execute before user clarification')
                tools.register(Tool('open_desktop_target','',{'type':'object','properties':{
                    'name':{'type':'string'},'type':{'type':'string'},'location':{'type':'string'}},
                    'required':['name','type']},PermissionLevel.ACTION,open_target))
                # The semantic provider tries both in one response. The second
                # candidate must never run, and no extra model round is allowed.
                provider=SimpleNamespace(chat=Mock(return_value=LLMResponse('',[
                    ToolCall('a','open_desktop_target',{'name':'Onyx','type':'folder','location':'desktop'}),
                    ToolCall('b','open_desktop_target',{'name':'онкис','type':'folder','location':'desktop'})])))
                agent=Orchestrator(provider,'test',tools,db,logging.getLogger('test'),StateMachine())
                chunks=[]
                answer=agent.respond('не закрывай VS Code, открой папку Onyx на рабочем столе',on_token=chunks.append)
                self.assertEqual(answer,question)
                self.assertEqual(chunks,[question])
                self.assertEqual(calls,['Onyx'])
                self.assertEqual(provider.chat.call_count,1)
                self.assertEqual(agent.last_task.status,TaskStatus.WAITING)
                self.assertEqual(agent.last_task.actions[-1]['result']['data']['status'],'needs_clarification')
            finally: db.close()

    def test_full_final_transcript_falls_back_without_loss_or_noun_echo(self):
        with TemporaryDirectory() as directory:
            db=MemoryDatabase(Path(directory)/'memory.db')
            try:
                tools=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(directory)/'STOP'),db,logging.getLogger('test'))
                tools.execute=Mock(return_value=ToolResult(True,{}))
                provider=SimpleNamespace(chat=Mock(side_effect=[LLMResponse('VS'),LLMResponse('Не закрывала VS Code. Уточни, какую папку открыть?')]))
                orchestrator=Orchestrator(provider,'test',tools,db,logging.getLogger('test'),StateMachine())
                text='не закрывай VS Code, открой папку'
                token=CancellationToken(diagnostic=True)
                token.voice_evidence=Transcription(text,'ru',1,1,.1,committed=True,raw_transcript=text)
                # A clarification requires a read-only observation pass; each
                # subsequent model response is complete and has no noun echo.
                provider.chat.side_effect=None
                provider.chat.return_value=LLMResponse('Не закрывала VS Code. Папка не указана, поэтому действие не выполнено.')
                chunks=[]
                answer=orchestrator.respond(text,on_token=chunks.append,cancellation=token)
                self.assertEqual(''.join(chunks),answer)
                self.assertTrue(any(m.get('role')=='user' and m.get('content')==text for m in provider.chat.call_args.args[1]))
                self.assertEqual(orchestrator.last_task.final_transcript,text)
                self.assertEqual(orchestrator.last_task.metrics['fast_route_status'],'not_handled')
                self.assertTrue(any(e['event']=='planner_fallback' and e['final_transcript']==text for e in token.trace))
            finally: db.close()
