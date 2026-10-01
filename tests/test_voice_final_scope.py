import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from kk.core.desktop_commands import parse_plan
from kk.core.literal_intents import literal_intent
from kk.core.request_scope import request_scope, validate_action
from kk.core.task import AgentTask, CancellationToken
from kk.core.task_batch import TaskBatch
from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.tools.registry import Tool, ToolRegistry


class VoiceFinalScopeTests(TestCase):
    def test_english_target_and_monitor_are_separate(self):
        for phrase in ('Onyx, open Notepad on the second monitor', 'Open Notepad on the 2 screen'):
            self.assertEqual(parse_plan(phrase), [dict(kind='open', app='Notepad', monitor=2)])

    def test_english_close_word_order_and_politeness(self):
        for phrase in ('Close VS Code, please', 'VS Code close', 'please close VS Code'):
            self.assertEqual(parse_plan(phrase), [dict(kind='window', operation='close', app='VS Code')])

    def test_mixed_literal_is_not_an_application(self):
        for phrase, app, payload in (
            ('Открой Obsidian and type: meeting at five','Obsidian','meeting at five'),
            ('Open Notepad and type: сегодня я изучаю Python','Notepad','сегодня я изучаю Python'),
            ('Напиши в Блокноте: не закрывай VS Code','Блокноте','не закрывай VS Code')):
            self.assertEqual(literal_intent(phrase), dict(app=app, text=payload, submit=False))
            self.assertIsNone(parse_plan(phrase))
            self.assertFalse(request_scope(phrase)['restricted'])

    def test_polite_negative_is_a_positive_request(self):
        self.assertEqual(parse_plan('Не мог бы ты открыть Блокнот?'), parse_plan('открой Блокнот'))
        self.assertFalse(request_scope('Не мог бы ты открыть Блокнот?')['restricted'])

    def test_correction_discards_original_target(self):
        phrase='Открой Firefox... нет, лучше Obsidian'
        self.assertEqual(parse_plan(phrase), parse_plan('Открой Obsidian'))
        scope=request_scope(phrase)
        validate_action(scope,'open_application',{'name':'Obsidian'})
        with self.assertRaises(PermissionError): validate_action(scope,'open_application',{'name':'Firefox'})

    def test_negations_preserve_allowed_actions(self):
        for phrase in ('Не закрывай VS Code, открой Блокнот', "Don't close Firefox; open Notepad instead"):
            scope=request_scope(phrase)
            validate_action(scope,'open_application',{'name':'notepad'})
            for tool,args in [('close_application',{'query':'VS Code'}),('close_window',{'hwnd':123}),
                              ('run_terminal',{'command':'anything'}),('press_hotkey',{'keys':['alt','f4']})]:
                with self.assertRaises(PermissionError): validate_action(scope,tool,args)

    def test_signed_fast_route_executes_only_allowed_clause_and_keeps_batch_scope(self):
        from kk.core.fast_commands import route_fast
        from kk.core.state import StateMachine
        from kk.tools.registry import ToolResult
        for phrase in ('Не закрывай VS Code, открой Блокнот', "Don't close Firefox; open Notepad instead"):
            tools=SimpleNamespace(execute=Mock(return_value=ToolResult(True,{'verified':True,'hwnd':123})))
            task=AgentTask(phrase)
            result=route_fast(phrase,task,tools,StateMachine())
            self.assertEqual(result.status,'handled')
            self.assertEqual(tools.execute.call_count,1)
            self.assertEqual(tools.execute.call_args.args[0],'open_application')
            self.assertEqual(task.batch.original_transcript,phrase)
            self.assertTrue(task.batch.constraints['prohibited'])

    def test_conflicting_same_target_is_not_authorized(self):
        for text in ("Don't open Discord; open Discord",'Не открывай Discord, открой Discord'):
            with self.assertRaises(PermissionError):
                validate_action(request_scope(text),'open_application',{'name':'Discord'})

    def test_withdrawn_and_negative_requests_authorize_no_mutation(self):
        from kk.core.orchestrator import requests_action
        for phrase in ('Не закрывай VS Code', "Don't close VS Code", 'Открой Discord... нет, не открывай'):
            self.assertFalse(requests_action(phrase))
            for name,args in [('close_application',{'query':'VS Code'}),('open_application',{'name':'Discord'})]:
                with self.assertRaises(PermissionError): validate_action(request_scope(phrase),name,args)

    def test_real_registry_blocks_adversarial_planner_calls_before_handler(self):
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'memory.db')
            try:
                registry=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'STOP'),db,logging.getLogger('test'))
                close,opened=Mock(return_value={'verified':True}),Mock(return_value={'verified':True})
                schema={'type':'object','properties':{'name':{'type':'string'}},'required':['name']}
                for name,handler in [('close_application',close),('open_application',opened)]:
                    registry.register(Tool(name,'',schema,PermissionLevel.ACTION,handler))
                for phrase in ('Не закрывай VS Code, открой Блокнот', "Don't close Firefox; open Notepad instead"):
                    token=CancellationToken(); token.user_request=phrase
                    task=AgentTask(phrase,cancellation_token=token)
                    self.assertTrue(task.request_scope['prohibited'])
                    binding=registry.cancellation.set(token)
                    try:
                        self.assertFalse(registry.execute('close_application',{'name':'VS Code'}).ok)
                        self.assertTrue(registry.execute('open_application',{'name':'Notepad'}).ok)
                    finally: registry.cancellation.reset(binding)
                close.assert_not_called()
                self.assertEqual(opened.call_count,2)
            finally: db.close()

    def test_unverified_human_voice_cannot_authorize_irreversible_action(self):
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'memory.db')
            try:
                registry=ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS,Path(folder)/'STOP'),db,logging.getLogger('test'))
                handler=Mock(return_value={'verified':True})
                registry.register(Tool('delete','',{'type':'object','properties':{}},PermissionLevel.DANGEROUS,handler))
                token=CancellationToken(); token.user_request='delete my test file'
                token.voice_evidence=SimpleNamespace(accepted=True,committed=True)
                binding=registry.cancellation.set(token)
                try:
                    self.assertFalse(registry.execute('delete',{}).ok)
                    handler.assert_not_called()
                    token.voice_evidence=None
                    self.assertTrue(registry.execute('delete',{}).ok)
                    handler.assert_called_once()
                finally: registry.cancellation.reset(binding)
            finally: db.close()

    def test_disagreement_question_names_folder_not_app(self):
        from kk.voice.recognition import fragment_question
        result=SimpleNamespace(text='не закрывай VS код, открой папку Onyx на рабочем столе',
            rejection_reason='passes_disagree',passes=[{'normalized_transcript':t} for t in
            ('не закрывай VS Code, открой папку Оникс на рабочем столе',
             'не закрывай VS код, открой папку Onyx на рабочем столе')])
        question=fragment_question(result)
        self.assertIn('папки',question)
        self.assertIn('«Оникс» или «Onyx»',question)

    def test_positive_pair_is_not_blocked(self):
        for phrase in ('Close VS Code, please','Закрой VS Code','Открой Discord','Напиши ifconfig'):
            validate_action(request_scope(phrase),'close_application',{'query':'VS Code'})
