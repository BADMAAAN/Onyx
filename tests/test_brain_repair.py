"""Offline regressions for production provider and conversation handling."""
import io
import json
import logging
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from kk.core.orchestrator import Orchestrator
from kk.core.state import StateMachine
from kk.core.task import CancellationToken
from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.providers.base import LLMResponse, ProviderError
from kk.providers.ollama_provider import OllamaProvider
from kk.providers.resources import ModelResourceManager, PressureGuard, ResourcePressure
from kk.tools.registry import ToolRegistry, Tool


class ProviderRepairTests(TestCase):
    def stream(self, events, **kwargs):
        raw = io.BytesIO(b"\n".join(json.dumps(event, ensure_ascii=False).encode("utf-8") for event in events))
        with patch("kk.providers.ollama_provider.urlopen", return_value=raw):
            return OllamaProvider("http://test").chat_stream("qwen3:14b", [], **kwargs)[0]

    def test_content_thinking_and_tool_call_in_same_event_are_independent(self):
        chunks = []
        events = [{"message": {"thinking": "private reasoning"}},
                  {"message": {"thinking": "private too", "content": "Проверю ", "tool_calls": [
                      {"function": {"name": "get_volume", "arguments": {}}}]}},
                  {"message": {"content": "громкость."}, "done": True, "done_reason": "stop"}]
        response = self.stream(events, on_token=chunks.append)
        self.assertEqual(response.content, "Проверю громкость.")
        self.assertEqual("".join(chunks), response.content)
        self.assertEqual([c.name for c in response.tool_calls], ["get_volume"])
        self.assertNotIn("private", response.content)

    def test_missing_done_discards_partial_result(self):
        with self.assertRaisesRegex(ProviderError, "before done"):
            self.stream([{"message": {"content": "Из"}}])

    def test_token_limit_is_not_successful_completion(self):
        with self.assertRaisesRegex(ProviderError, "length"):
            self.stream([{"message": {"content": "Из"}, "done": True, "done_reason": "length"}])

    def test_thinking_only_done_is_not_a_user_answer(self):
        with self.assertRaisesRegex(ProviderError, "empty"):
            self.stream([{"message": {"thinking": "hidden"}, "done": True, "done_reason": "stop"}])

    def test_tool_arguments_must_be_valid_before_execution(self):
        with self.assertRaisesRegex(ProviderError, "arguments"):
            self.stream([{"message": {"tool_calls": [{"function": {"name": "type_text", "arguments": "{bad"}}]}, "done": True}])

    def test_done_terminates_without_consuming_later_transport_data(self):
        @contextmanager
        def transport(*args, **kwargs):
            def events():
                yield b'{"message":{"content":"Complete."},"done":true,"done_reason":"stop"}\n'
                raise AssertionError("read after terminal event")
            yield events()
        with patch("kk.providers.ollama_provider.interruptible_request", transport):
            response, _ = OllamaProvider("http://test").chat_stream("test", [])
        self.assertEqual(response.content, "Complete.")

    def test_utf8_split_across_network_reads_preserves_final_tail(self):
        payload = b"\n".join(json.dumps(event, ensure_ascii=False).encode("utf-8") for event in [
            {"message": {"content": "Чай делают "}},
            {"message": {"content": "из листьев 🍃"}, "done": True, "done_reason": "stop"}])
        class NetworkFragments(io.RawIOBase):
            def __init__(self): self.position = 0
            def readable(self): return True
            def readinto(self, buffer):
                chunk = payload[self.position:self.position + min(len(buffer), 3)]
                buffer[:len(chunk)] = chunk
                self.position += len(chunk)
                return len(chunk)
        raw = io.BufferedReader(NetworkFragments(), buffer_size=7)
        with patch("kk.providers.ollama_provider.urlopen", return_value=raw):
            response, _ = OllamaProvider("http://test").chat_stream("test", [])
        self.assertEqual(response.content, "Чай делают из листьев 🍃")

    def test_blank_protocol_lines_do_not_finish_response(self):
        raw = io.BytesIO(b'\n' + b'{"message":{"content":"Complete."},"done":true}\n')
        with patch('kk.providers.ollama_provider.urlopen', return_value=raw):
            response, _ = OllamaProvider('http://test').chat_stream('test', [])
        self.assertEqual(response.content, 'Complete.')

    def test_malformed_network_json_is_a_provider_error(self):
        raw = io.BytesIO(b'{not json}\n')
        with patch('kk.providers.ollama_provider.urlopen', return_value=raw):
            with self.assertRaisesRegex(ProviderError, 'UTF-8/JSON'):
                OllamaProvider('http://test').chat_stream('test', [])

    def test_nonstream_also_rejects_truncation(self):
        raw = {"message": {"content": "Из"}, "done": True, "done_reason": "length"}
        with patch("kk.providers.ollama_provider.request_json", return_value=raw):
            with self.assertRaisesRegex(ProviderError, "length"):
                OllamaProvider("http://test").chat("test", [])

    def test_diagnostic_request_is_an_immutable_snapshot_of_sent_history(self):
        token = CancellationToken(diagnostic=True)
        messages = [{'role': 'user', 'content': 'Original request', 'metadata': {'nested': ['original']}}]
        raw = io.BytesIO(b'{"message":{"content":"Complete."},"done":true}\n')
        with patch('kk.providers.ollama_provider.urlopen', return_value=raw):
            OllamaProvider('http://test').chat_stream('test', messages, cancellation=token)
        messages[0]['metadata']['nested'].append('later mutation')
        messages.append({'role': 'assistant', 'content': 'Later answer'})
        request = next(event for event in token.trace if event['event'] == 'brain_request')
        self.assertEqual(request['message_count'], 1)
        self.assertEqual(request['messages'], [{'role': 'user', 'content': 'Original request', 'metadata': {'nested': ['original']}}])

    def test_trace_text_requires_explicit_diagnostic_opt_in(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                token = CancellationToken()
                token.diagnostic = enabled
                self.stream([{"message": {"content": "Private response."}, "done": True, "done_reason": "stop"}], cancellation=token)
                event = next(row for row in token.trace if row['event'] == 'brain_response')
                self.assertEqual(event['done_reason'], 'stop')
                self.assertEqual(event.get('content'), 'Private response.' if enabled else None)


class ConversationRepairTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = MemoryDatabase(Path(self.temp.name) / 'data' / 'memory.db')
        self.registry = ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS, Path(self.temp.name) / 'stop'), self.db, logging.getLogger('brain-test'))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def agent(self, provider):
        return Orchestrator(provider, 'fake', self.registry, self.db, logging.getLogger('brain-test'), StateMachine())

    def test_failed_turn_does_not_poison_next_history_and_logs_traceback(self):
        provider = SimpleNamespace(chat=Mock(side_effect=[ProviderError('test backend failure'), LLMResponse('Ответ завершён.')]))
        agent = self.agent(provider)
        agent.short_term.add('user', 'Первый вопрос')
        agent.short_term.add('assistant', 'Первый ответ')
        with self.assertLogs('brain-test', 'ERROR') as captured:
            with self.assertRaises(ProviderError): agent.respond('Вопрос с ошибкой')
        self.assertIn('Traceback', '\n'.join(captured.output))
        self.assertEqual(agent.short_term.snapshot(), [{'role': 'user', 'content': 'Первый вопрос'}, {'role': 'assistant', 'content': 'Первый ответ'}])
        agent.respond('Новый вопрос')
        sent = provider.chat.call_args.args[1]
        self.assertNotIn('Вопрос с ошибкой', [m.get('content') for m in sent])

    def test_question_with_bare_preposition_retries_without_speaking_fragment(self):
        chunks = []
        provider = SimpleNamespace(chat=Mock(side_effect=[LLMResponse('Из'), LLMResponse('Чай делают из листьев чайного растения.')]))
        agent = self.agent(provider)
        answer = agent.respond('Из чего делают чай?', on_token=chunks.append)
        self.assertEqual(answer, 'Чай делают из листьев чайного растения.')
        self.assertEqual(chunks, [answer])
        self.assertEqual(provider.chat.call_count, 2)

    def test_short_complete_explanation_is_not_padded_to_word_minimum(self):
        provider = SimpleNamespace(chat=Mock(return_value=LLMResponse('Лёд — замёрзшая вода. При нагревании он тает.')))
        agent = self.agent(provider)
        answer = agent.respond('Объясни, что такое лёд')
        self.assertEqual(answer, 'Лёд — замёрзшая вода. При нагревании он тает.')
        self.assertEqual(provider.chat.call_count, 1)

    def test_stream_retry_discards_prefix_and_flushes_unpunctuated_final_tail(self):
        responses = iter([['Из'], ['Чай делают ', 'из листьев чайного растения']])
        def chat_stream(model, messages, tools, temperature, on_token=None, cancellation=None):
            chunks = next(responses)
            for chunk in chunks:
                if on_token: on_token(chunk)
            return LLMResponse(''.join(chunks)), {'first_token_seconds': .01, 'total_seconds': .02}
        agent = self.agent(SimpleNamespace(chat_stream=chat_stream))
        spoken = []
        answer = agent.respond('Из чего делают чай?', on_token=spoken.append)
        self.assertEqual(spoken, [answer])
        self.assertEqual(answer, 'Чай делают из листьев чайного растения')
        self.assertEqual(agent.last_task.retries, 1)

    def test_explanation_streams_sentences_before_done_but_discards_gpt_fragment(self):
        spoken = []
        rounds = iter([['GPT'], ['ChatGPT — языковая модель. ', 'Она помогает отвечать на вопросы и работать с текстом.']])
        early = []
        def chat_stream(model, messages, tools, temperature, on_token=None, cancellation=None):
            chunks = next(rounds)
            for chunk in chunks:
                if on_token: on_token(chunk)
                early.append(''.join(spoken))
            return LLMResponse(''.join(chunks)), {'first_token_seconds': .01, 'total_seconds': .02}
        agent = self.agent(SimpleNamespace(chat_stream=chat_stream))
        answer = agent.respond('Расскажи про ChatGPT простыми словами', on_token=spoken.append)
        self.assertEqual(early[0], '')
        self.assertEqual(early[1], 'ChatGPT — языковая модель. ')
        self.assertEqual(''.join(spoken), answer)
        self.assertEqual(agent.last_task.retries, 1)

    def test_repeated_fragment_fails_and_is_never_spoken(self):
        agent = self.agent(SimpleNamespace(chat=Mock(return_value=LLMResponse('Ну'))))
        spoken = []
        with self.assertLogs('brain-test', 'ERROR'):
            with self.assertRaisesRegex(ProviderError, 'fragmentary'):
                agent.respond('Из чего делают чай?', on_token=spoken.append)
        self.assertEqual(spoken, [])
        self.assertEqual(agent.short_term.snapshot(), [])
        self.assertEqual(agent.last_task.status.value, 'FAILED')

    def test_fast_literal_keeps_verified_window_identity_in_context(self):
        window = {'hwnd': 123, 'process_id': 456, 'monitor_index': 2, 'process': 'powershell.exe', 'title': 'PowerShell'}
        self.registry.register(Tool('open_application', '', {'type': 'object', 'properties': {'name': {'type': 'string'}}},
            PermissionLevel.ACTION, lambda name: dict(window, verified=True, window=window)))
        self.registry.register(Tool('text_entry', '', {'type': 'object', 'properties': {'text': {'type': 'string'}, 'submit': {'type': 'boolean'}}},
            PermissionLevel.ACTION, lambda text, submit: {'verified': True, 'hwnd': 123}))
        agent = self.agent(SimpleNamespace(chat=Mock(side_effect=AssertionError('unnecessary model call'))))
        with patch('kk.tools.windows.native.foreground_window', return_value=window):
            self.assertEqual(agent.respond('Открой PowerShell и напиши ifconfig'), 'Написала.')
        context = self.registry.desktop_context
        self.assertEqual((context.hwnd, context.process_id, context.last_monitor), (123, 456, 2))
        self.assertEqual(context.last_window, window)
        self.assertEqual(context.last_entity, 'PowerShell')

    def test_explicit_one_word_answer_remains_allowed(self):
        agent = self.agent(SimpleNamespace(chat=Mock(return_value=LLMResponse('Из'))))
        self.assertEqual(agent.respond('Ответь одним словом: какой здесь предлог?'), 'Из')

    def test_how_to_questions_do_not_force_actions_or_desktop_observation(self):
        from kk.core.orchestrator import requests_action
        for request in ('Как открыть Firefox?', 'Как включить музыку?', 'Можно ли открыть Firefox?',
                        'Объясни, как перенести браузер на второй монитор', 'Подскажи, как закрыть окно'):
            with self.subTest(request=request):
                provider = SimpleNamespace(chat=Mock(return_value=LLMResponse('Используйте меню приложения. Там можно выбрать нужную команду.')))
                agent = self.agent(provider)
                with patch.object(self.registry, 'execute', side_effect=AssertionError('question must not inspect or change desktop')):
                    agent.respond(request)
                self.assertFalse(requests_action(request))
                self.assertIsNone(provider.chat.call_args.args[2])
                self.assertEqual(provider.chat.call_count, 1)
                self.assertEqual(agent.last_task.status.value, 'COMPLETED')
        for request in ('Открой Firefox и объясни, как включить музыку', 'Открой Firefox, что на первом экране?', 'Можешь открыть Firefox?'):
            self.assertTrue(requests_action(request), request)

    def test_ordinary_app_questions_do_not_advertise_action_tools(self):
        for request in ('Что такое Firefox?', 'Расскажи про ChatGPT простыми словами', 'Чем музыка отличается от шума?'):
            with self.subTest(request=request):
                provider = SimpleNamespace(chat=Mock(return_value=LLMResponse('Это полный ответ. Он объясняет предмет вопроса.')))
                agent = self.agent(provider)
                agent.respond(request)
                self.assertIsNone(provider.chat.call_args.args[2])
                self.assertEqual(agent.last_task.actions, [])


class ResourceRecoveryTests(TestCase):
    def resident_replay_manager(self):
        gb = 1024 ** 3
        provider = SimpleNamespace(base_url='http://test', context_window=8192,
            list_models=lambda: [{'name': 'qwen3:14b', 'size': 9 * gb}], unload_model=Mock())
        manager = ModelResourceManager(provider, vram_reserve_gb=2.5)
        manager.model_profiles['qwen3:14b'] = {'context': 8192, 'resident_gb': 10.5}
        manager.snapshot = Mock(return_value={'ram_total': 32 * gb, 'ram_available': 15 * gb,
            'vram_total': 17103323136, 'vram_used': 17103323136 - 3303014400,
            'models': [{'name': 'qwen3:14b', 'size': 10468448009,
                        'size_vram': 10468448009, 'context_length': 8192}]})
        return manager

    def test_snapshot_keeps_driver_reported_free_memory(self):
        manager = self.resident_replay_manager()
        with patch('kk.providers.resources.request_json', return_value={'models': []}), patch(
                'kk.providers.resources.subprocess.check_output', return_value='16311, 3121, 12931\n') as query:
            state = ModelResourceManager.snapshot(manager)
        self.assertEqual(state['vram_free'], 12931 * 1024 ** 2)
        self.assertLess(state['vram_free'], state['vram_total'] - state['vram_used'])
        self.assertIn('--query-gpu=memory.total,memory.used,memory.free', query.call_args.args[0])

    def test_snapshot_owns_handles_in_redirected_background_voice_runtime(self):
        def redirected_probe(*args, **kwargs):
            if kwargs.get('stderr') != subprocess.PIPE or kwargs.get('stdin') != subprocess.DEVNULL:
                raise OSError(50, 'non-duplicable inherited Windows handle')
            return '16311, 3121, 12931\n'
        manager = self.resident_replay_manager()
        with patch('kk.providers.resources.request_json', return_value={'models': []}), patch(
                'kk.providers.resources.subprocess.check_output', side_effect=redirected_probe):
            state = ModelResourceManager.snapshot(manager)
        self.assertEqual(state['vram_free'], 12931 * 1024 ** 2)
        self.assertNotIn('gpu_error', state)

    def test_gpu_telemetry_failure_stays_diagnostic_and_blocks_stt(self):
        for output in ('', 'not numeric', '16311, 3121'):
            with self.subTest(output=output):
                manager = ModelResourceManager(SimpleNamespace(base_url='http://test'))
                with patch('kk.providers.resources.request_json', return_value={'models': []}), patch(
                        'kk.providers.resources.subprocess.check_output', return_value=output), patch(
                        'kk.providers.resources.process_model_lock', return_value=nullcontext()):
                    with self.assertRaisesRegex(ResourcePressure, 'Cannot verify resources before STT loading:'):
                        manager.prepare_stt()
                self.assertIn('gpu_error', manager.last_snapshot)
                self.assertNotIn('vram_total', manager.last_snapshot)

    def test_ollama_telemetry_failure_is_reported_before_stt(self):
        manager = ModelResourceManager(SimpleNamespace(base_url='http://test'))
        with patch('kk.providers.resources.request_json', side_effect=OSError('Ollama offline')), patch(
                'kk.providers.resources.subprocess.check_output', return_value='16311, 3121, 12931\n'), patch(
                'kk.providers.resources.process_model_lock', return_value=nullcontext()):
            with self.assertRaisesRegex(ResourcePressure, 'Ollama offline'):
                manager.prepare_stt()

    def test_reported_free_blocks_resident_when_total_minus_used_would_pass(self):
        manager = self.resident_replay_manager()
        state = manager.snapshot.return_value
        state['vram_used'] = state['vram_total'] - int(2.74 * 1024 ** 3)
        state['vram_free'] = int(2.49 * 1024 ** 3)
        with patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
            with self.assertRaisesRegex(ResourcePressure, 'VRAM reserve'):
                manager.preflight('qwen3:14b')

    def test_calibrated_cold_profile_fits_driver_reported_free(self):
        manager = self.resident_replay_manager()
        manager.model_profiles['qwen3:14b']['resident_gb'] = 10.0
        state = manager.snapshot.return_value
        state.update(models=[], vram_total=16311 * 1024 ** 2, vram_used=3121 * 1024 ** 2,
                     vram_free=12931 * 1024 ** 2)
        with patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
            self.assertEqual(manager.preflight('qwen3:14b')['projected_model_bytes'], 10 * 1024 ** 3)
            state['vram_free'] = int(12.49 * 1024 ** 3)
            with self.assertRaisesRegex(ResourcePressure, 'VRAM reserve'):
                manager.preflight('qwen3:14b')

    def test_stt_reserve_uses_driver_reported_free(self):
        manager = self.resident_replay_manager()
        state = manager.snapshot.return_value
        state.update(models=[], vram_free=int(4.49 * 1024 ** 3))
        state['vram_used'] = state['vram_total'] - int(4.74 * 1024 ** 3)
        with patch('kk.providers.resources.process_model_lock', return_value=nullcontext()):
            with self.assertRaisesRegex(ResourcePressure, 'STT loading'):
                manager.prepare_stt()

    def test_runtime_guard_uses_driver_reported_free(self):
        manager = self.resident_replay_manager()
        state = manager.snapshot.return_value
        state['vram_used'] = state['vram_total'] - int(2.74 * 1024 ** 3)
        state['vram_free'] = int(2.49 * 1024 ** 3)
        with patch('kk.providers.resources.psutil.virtual_memory', return_value=SimpleNamespace(available=15 * 1024 ** 3, percent=50)), patch(
                'kk.providers.resources.psutil.swap_memory', return_value=SimpleNamespace(used=0)), patch(
                'kk.providers.resources.psutil.disk_io_counters', return_value=SimpleNamespace(read_bytes=0, write_bytes=0)), patch(
                'kk.providers.resources.responsiveness', return_value={'responsive': True}), patch(
                'kk.providers.resources.request_json', return_value={'models': state['models']}):
            guard = PressureGuard(manager, 'qwen3:14b')
            guard.done.wait = Mock(side_effect=[False, True])
            guard._watch()
        self.assertTrue(guard.aborted.is_set())
        self.assertIn('VRAM headroom below', guard.reason)

    def test_matching_resident_context_uses_actual_allocation_not_cold_profile(self):
        manager = self.resident_replay_manager()
        with patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
            state = manager.preflight('qwen3:14b')
        self.assertEqual(state['projected_model_bytes'], 10468448009)
        self.assertEqual(state['projected_offload_bytes'], 0)
        self.assertEqual(manager.vram_reserve, int(2.5 * 1024 ** 3))

    def test_matching_resident_model_still_requires_real_vram_reserve(self):
        manager = self.resident_replay_manager()
        state = manager.snapshot.return_value
        state['vram_used'] = state['vram_total'] - int(2.49 * 1024 ** 3)
        with patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
            with self.assertRaisesRegex(ResourcePressure, 'VRAM reserve'):
                manager.preflight('qwen3:14b')

    def test_cold_unknown_and_resized_context_keep_conservative_projection(self):
        for context in (None, 4096, 'cold'):
            with self.subTest(context=context):
                manager = self.resident_replay_manager()
                state = manager.snapshot.return_value
                if context == 'cold':
                    state['vram_used'] -= state['models'][0]['size_vram']
                    state['models'] = []
                else:
                    state['models'][0]['context_length'] = context
                with patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
                    with self.assertRaisesRegex(ResourcePressure, 'VRAM reserve'):
                        manager.preflight('qwen3:14b')

    def test_observed_ram_offload_is_not_hidden_by_actual_gpu_allocation(self):
        manager = self.resident_replay_manager()
        manager.snapshot.return_value['models'][0]['size'] += 2 * 1024 ** 3
        with patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
            with self.assertRaisesRegex(ResourcePressure, 'offload exceeds'):
                manager.preflight('qwen3:14b')

    def test_observed_size_for_smaller_context_is_not_reused_for_resize(self):
        manager = self.resident_replay_manager()
        manager.model_profiles.clear()
        with patch('kk.providers.resources.process_model_lock', return_value=nullcontext()), patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
            with manager.lease('qwen3:14b'):
                pass
            manager.provider.context_window = 16384
            with self.assertRaisesRegex(ResourcePressure, 'VRAM reserve'):
                manager.preflight('qwen3:14b')

    def test_healthy_resident_model_is_not_evicted_when_turn_is_cancelled(self):
        gb = 1024 ** 3
        for resident in (False, True):
            with self.subTest(resident=resident):
                provider = SimpleNamespace(base_url='http://test', context_window=8192,
                    list_models=lambda: [{'name': 'fast', 'size': 8 * gb}], unload_model=Mock())
                manager = ModelResourceManager(provider)
                manager.snapshot = Mock(return_value={'ram_total': 32 * gb, 'ram_available': 20 * gb,
                    'vram_total': 16 * gb, 'vram_used': (10 if resident else 2) * gb,
                    'models': [{'name': 'fast', 'size': 8 * gb, 'size_vram': 8 * gb}] if resident else []})
                manager.ensure_unloaded = Mock()
                with patch('kk.providers.resources.process_model_lock', return_value=nullcontext()), patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
                    with self.assertRaises(InterruptedError):
                        with manager.lease('fast'):
                            raise InterruptedError('confirmed_barge_in')
                if resident:
                    manager.ensure_unloaded.assert_not_called()
                    self.assertEqual(manager.warm_states['fast']['state'], 'warm')
                else:
                    manager.ensure_unloaded.assert_called_once_with('fast')
                    self.assertEqual(manager.warm_states['fast']['state'], 'unloaded')

    def test_transient_abort_can_recover_but_requires_fresh_safe_preflight(self):
        gb = 1024 ** 3
        provider = SimpleNamespace(base_url='http://test', context_window=8192,
            list_models=lambda: [{'name': 'fast', 'size': 8 * gb}], unload_model=Mock())
        manager = ModelResourceManager(provider)
        manager.snapshot = Mock(return_value={'ram_total': 32 * gb, 'ram_available': 20 * gb,
            'vram_total': 16 * gb, 'vram_used': 2 * gb, 'models': []})
        manager.ensure_unloaded = Mock()
        with patch('kk.providers.resources.process_model_lock', return_value=nullcontext()), patch('kk.providers.resources.responsiveness', return_value={'responsive': True}):
            with self.assertRaises(ResourcePressure):
                with manager.lease('fast'):
                    manager.guard.fail('Foreground unresponsive in two consecutive 150ms probes')
            with self.assertRaisesRegex(ResourcePressure, 'cooldown'):
                manager.preflight('fast')
            manager.transient_failures['fast']['at'] -= manager.recovery_cooldown_seconds + 1
            manager.snapshot.return_value['ram_available'] = 7 * gb
            with self.assertRaisesRegex(ResourcePressure, '9 GB'):
                manager.preflight('fast')
            manager.snapshot.return_value['ram_available'] = 20 * gb
            self.assertEqual(manager.preflight('fast')['ram_available'], 20 * gb)
            manager.provider.unload_model.assert_not_called()
