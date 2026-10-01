from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kk.core.control import RuntimeControl, request_control
from kk.core.task import CancellationToken, AgentTask
from kk.core.brain import ToolCapabilityRouter, ContextBroker
from kk.voice.prosody import prepare
from kk.voice.speech_queue import SpeechQueue


class IntegrationRuntimeTests(TestCase):
    def test_memory_write_is_action_and_success_requires_evidence(self):
        from kk.core.orchestrator import requests_action, claims_success
        from kk.core.desktop_commands import parse_plan
        for text in ('Сохрани предпочтение pronunciation.Nebula: Нэбула',
                     'Запомни мой браузер Firefox', 'Забудь pronunciation.Nebula'):
            self.assertTrue(requests_action(text),text)
        self.assertFalse(requests_action('Как сохранить предпочтение?'))
        self.assertTrue(claims_success('Предпочтение сохранено.'))
        self.assertIsNone(parse_plan('Запомни предпочтение pronunciation.Nebula: Нэбула. Это временная тестовая настройка.'))

    def test_local_control_authentication_and_cleanup(self):
        with TemporaryDirectory() as folder:
            received = []
            server = RuntimeControl(folder, lambda request: received.append(request) or {'state':'ready'})
            server.start()
            try:
                self.assertEqual(server.server.server_address[0], '127.0.0.1')
                request = Request(f'http://127.0.0.1:{server.server.server_port}/control', data=b'{}', method='POST')
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, 403)
                self.assertFalse(received)
                self.assertEqual(request_control(folder, 'status'), {'state':'ready'})
                self.assertEqual(received, [{'operation':'status'}])
                metadata = json.loads(server.path.read_text())
                metadata['created'] -= 50
                server.path.write_text(json.dumps(metadata))
                with self.assertRaisesRegex(RuntimeError, 'Stale'):
                    request_control(folder, 'status')
            finally:
                server.close()
            self.assertFalse(server.path.exists())

    def test_latin_only_speech_remains_audible_without_changing_payload(self):
        original = 'OK, GPT, Python и print("hello")'
        converted = prepare(original)
        self.assertEqual(original, 'OK, GPT, Python и print("hello")')
        self.assertIn('Окей', converted)
        self.assertIn('Джи Пи Ти', converted)
        self.assertNotRegex(converted, r'[A-Za-z]')
        self.assertEqual(prepare('SDK'), 'эс ди кей')
        self.assertEqual(prepare('Onyx', {'Onyx':'О́никс'}), 'О́никс')

    def test_punctuation_chunk_does_not_cancel_following_speech(self):
        tts = SimpleNamespace(muted=True, sample_rate=48000, volume=1.)
        queue = SpeechQueue(tts)
        try:
            token = queue.begin(CancellationToken())
            queue.submit('...', token)
            queue.submit('Законченная мысль без знака в конце', token)
            queue.finish(token)
            queue.wait(token, timeout=2)
            self.assertFalse(token.event.is_set())
            self.assertEqual(sum(e['event'] == 'tts_enqueue' for e in token.trace), 1)
            self.assertEqual(token.trace[-1]['event'], 'speech_complete')
        finally:
            queue.close()

    def test_schema_budget_preserves_explicit_discovery(self):
        rows = [{'type':'function','function':{'name':name, 'description':'x'*300,
                'parameters':{'type':'object','properties':{}}}}
                for name in ['discover_tools','observe_context','browser_control','unusual_tool','open_application']]
        selected = ToolCapabilityRouter().fit(rows, 'Открой Firefox', budget=800, requested=['unusual_tool'])
        names = [s['function']['name'] for s in selected]
        self.assertIn('unusual_tool', names)
        self.assertIn('discover_tools', names)
        self.assertLessEqual(sum(len(json.dumps(s,ensure_ascii=False)) for s in selected),800)

    def test_initial_history_bound_keeps_contract_and_current_goal(self):
        contract = 'Important contract.'
        current = {'role':'user','content':'Current explicit target'}
        messages = [{'role':'system','content':contract},
                    {'role':'user','content':'past '*4000},
                    {'role':'assistant','content':'answer '*4000}, current]
        result = ContextBroker().compact_rounds(messages, AgentTask(current['content']), budget=1500)
        self.assertEqual(result[0]['content'], contract)
        self.assertEqual(result[-1], current)
        self.assertLessEqual(sum(len(m['content']) for m in result),1500)
