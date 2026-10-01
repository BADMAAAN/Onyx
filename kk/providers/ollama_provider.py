from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter
from typing import Any
import uuid
from urllib.request import Request, urlopen

from .base import LLMProvider, LLMResponse, ToolCall, ProviderError
from .http import request_json
from .resources import managed_model


from contextlib import contextmanager


@contextmanager
def interruptible_request(request, timeout, guard=None, cancellation=None):
    if guard is None:
        with urlopen(request, timeout=timeout) as response:
            yield response
        return
    # Register the connected socket BEFORE sending the model load request, so
    # pressure can cancel even while Ollama has not sent HTTP headers yet.
    from http.client import HTTPConnection, HTTPSConnection
    from urllib.parse import urlsplit
    import socket
    from threading import Event, Thread
    finished=Event()
    cancel_worker=None
    parts = urlsplit(request.full_url)
    connection = (HTTPSConnection if parts.scheme=='https' else HTTPConnection)(parts.hostname,parts.port,timeout=timeout)
    try:
        guard.check()
        connection.connect()
        transport = connection.sock
        def abort():
            if transport: transport.shutdown(socket.SHUT_RDWR)
        guard.abort_io = abort
        if cancellation:
            def watch_cancel():
                while not finished.wait(.1):
                    try: cancellation.check()
                    except (InterruptedError,TimeoutError):
                        try: abort()
                        except OSError: pass
                        return
            cancel_worker=Thread(target=watch_cancel,name='OnyxInferenceCancel',daemon=True)
            cancel_worker.start()
        guard.check()
        connection.request('POST',parts.path,body=request.data,headers=dict(request.header_items()))
        response = connection.getresponse()
        if response.status != 200:
            from .base import ProviderError
            raise ProviderError(f'Ollama HTTP {response.status}: '+response.read(1000).decode('utf-8','replace'))
        yield response
    except BaseException:
        if cancellation: cancellation.check()
        raise
    finally:
        finished.set()
        if cancel_worker: cancel_worker.join(.3)
        guard.abort_io = None
        connection.close()


def _message_parts(event):
    if not isinstance(event, dict):
        raise ProviderError('Ollama response must be a JSON object')
    if event.get('error'):
        raise ProviderError(str(event['error']))
    message = event.get('message') or {}
    if not isinstance(message, dict):
        raise ProviderError('Ollama message must be an object')
    content = message.get('content') or ''
    thinking = message.get('thinking') or ''
    calls = message.get('tool_calls') or []
    if not isinstance(content, str) or not isinstance(thinking, str) or not isinstance(calls, list):
        raise ProviderError('Ollama returned invalid message content/thinking/tool_calls')
    return content, thinking, calls


def _tool_call(call, fallback_id):
    function = call.get('function') if isinstance(call, dict) else None
    if not isinstance(function, dict) or not isinstance(function.get('name'), str) or not function['name']:
        raise ProviderError('Ollama tool call has no valid function name')
    arguments = function.get('arguments')
    if arguments is None:
        arguments = {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError as exc:
            raise ProviderError('Ollama tool arguments are not valid JSON') from exc
    if not isinstance(arguments, dict):
        raise ProviderError('Ollama tool arguments must be a JSON object')
    return ToolCall(call.get('id') or fallback_id, function['name'], arguments)


def _validate_completion(final, content, calls):
    if final.get('done') is not True:
        raise ProviderError('Ollama stream ended before done; incomplete response discarded')
    reason = final.get('done_reason')
    if reason not in (None, '', 'stop', 'tool_calls'):
        raise ProviderError(f'Ollama incomplete completion: done_reason={reason}')
    if not content.strip() and not calls:
        raise ProviderError('Ollama completed with empty user content and no tool calls')


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 180, context_window: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.context_window = context_window
        self.keep_alive = '30m'

    def is_available(self) -> bool:
        try:
            request_json(f"{self.base_url}/api/tags", timeout=3)
            return True
        except Exception:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        return request_json(f"{self.base_url}/api/tags", timeout=10).get("models", [])

    def chat(self, model, messages, tools=None, temperature=0.2) -> LLMResponse:
        manager=getattr(self,'model_manager',None)
        row=manager.verify(model) if manager else None
        if getattr(self, 'resource_manager', None):
            return self.chat_stream(model, messages, tools, temperature)[0]
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature},
            "think": False,
        }
        if self.context_window:
            body["options"]["num_ctx"] = self.context_window
        if any(message.get('images') for message in messages):
            body['options']['num_predict'] = 1024
            body['options']['num_ctx'] = min(self.context_window or 8192,8192)
        if tools:
            body["tools"] = tools
        if row:
            if 'thinking' not in row['capabilities']:body.pop('think',None)
            if 'tools' not in row['capabilities']:body.pop('tools',None)
            body['options']['num_ctx']=min(body['options'].get('num_ctx',8192),row['context_limit'] or 8192)
        raw = request_json(
            f"{self.base_url}/api/chat", method="POST", body=body, timeout=self.timeout
        )
        content, _, raw_calls = _message_parts(raw)
        if row and 'tools' not in row['capabilities'] and raw_calls:raise ProviderError('Selected model does not support tool calls')
        calls = [_tool_call(call, f'ollama-{index}') for index, call in enumerate(raw_calls)]
        _validate_completion(raw, content, calls)
        return LLMResponse(content, calls, raw)

    def describe_image(self, model: str, prompt: str, image_path: str, cancellation=None) -> str:
        return self.describe_image_bytes(model, prompt, Path(image_path).read_bytes(), cancellation=cancellation)

    def describe_image_bytes(self, model, prompt, image, cancellation=None):
        if not isinstance(image, bytes) or not image:
            raise ProviderError('Image payload must be nonempty raw image bytes, not a path or base64 string')
        encoded = base64.b64encode(image).decode("ascii")
        messages = [{"role": "user", "content": prompt, "images": [encoded]}]
        if cancellation is not None:
            cancellation.check()
            result = self.chat_stream(model, messages, temperature=0.1, cancellation=cancellation)[0]
        else:
            result = self.chat(model, messages, temperature=0.1)
        self.last_image_stats = {k:result.raw.get(k) for k in ('prompt_eval_count','eval_count','load_duration','prompt_eval_duration','eval_duration','total_duration')}
        if cancellation is not None:
            cancellation.mark('image_inference_complete', model=model, image_bytes=len(image),
                              **self.last_image_stats)
        return result.content

    def model_capabilities(self, model: str) -> list[str]:
        # /show inspects local metadata; it does not load a model or download it.
        return request_json(f'{self.base_url}/api/show', method='POST',
                            body={'model': model}, timeout=min(self.timeout, 10)).get('capabilities', [])

    def unload_model(self, model: str) -> None:
        if not model:
            return
        request_json(
            f"{self.base_url}/api/generate",
            method="POST",
            body={"model": model, "keep_alive": 0},
            timeout=min(self.timeout, 30),
        )

    def stream_benchmark(self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> tuple[LLMResponse, dict[str, float]]:
        return self.chat_stream(model, messages, tools)

    @managed_model
    def chat_stream(self, model, messages, tools=None, temperature=0.2, on_token=None, cancellation=None):
        manager=getattr(self,'model_manager',None)
        row=manager.verify(model, getattr(cancellation,'model_identity',{}).get('digest') if cancellation and getattr(cancellation,'model_identity',{}).get('model_id')==model else None) if manager else None
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_ctx": self.context_window or 8192},
            "think": False,
        }
        if tools:
            body["tools"] = tools
        if row:
            if 'thinking' not in row['capabilities']:body.pop('think',None)
            if 'tools' not in row['capabilities']:body.pop('tools',None)
            body['options']['num_ctx']=min(body['options']['num_ctx'],row['context_limit'] or 8192)
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        started = perf_counter()
        request_id = uuid.uuid4().hex
        if cancellation:
            cancellation.mark('brain_request_start', request_id=request_id)
        first_event = None
        first_content = None
        content: list[str] = []
        calls: list[ToolCall] = []
        final: dict[str, Any] = {}
        if any(m.get('images') for m in messages):
            body['options']['num_predict'] = 1024
            body['options']['num_ctx'] = min(body['options']['num_ctx'], 8192)
            request.data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if cancellation:
            diagnostic = {'messages': deepcopy(messages)} if getattr(cancellation, 'diagnostic', False) else {}
            cancellation.mark('brain_request', request_id=request_id, model=model,
                options=dict(body['options']), think=body.get('think'), keep_alive=body['keep_alive'],
                tool_count=len(body.get('tools', [])), message_count=len(messages), **diagnostic)
        manager = getattr(self, 'resource_manager', None)
        guard = manager.guard if manager else None
        with interruptible_request(request, timeout=self.timeout, guard=guard, cancellation=cancellation) as response:
            for raw_line in response:
                if guard: guard.check()
                if cancellation:
                    cancellation.check()
                if not raw_line.strip():
                    continue
                try:
                    event = json.loads(raw_line.decode('utf-8'))
                except (ValueError, UnicodeError) as exc:
                    raise ProviderError('Ollama stream contains invalid UTF-8/JSON') from exc
                chunk, thinking, tool_chunks = _message_parts(event)
                if row and 'tools' not in row['capabilities'] and tool_chunks:raise ProviderError('Selected model does not support tool calls')
                if first_event is None and (chunk or thinking or tool_chunks):
                    first_event = perf_counter()
                    if cancellation:
                        cancellation.mark('brain_first_token')
                if chunk and first_content is None:
                    first_content = perf_counter()
                    if cancellation:
                        cancellation.mark('brain_first_content', request_id=request_id)
                content.append(chunk)
                if chunk and on_token:
                    on_token(chunk)
                for index, call in enumerate(tool_chunks):
                    if cancellation:
                        cancellation.mark('intent_decided',route='brain_tool')
                    calls.append(_tool_call(call, f'{request_id}-{len(calls)}'))
                if event.get('done') is True:
                    final = event
                    break
        complete_content = ''.join(content)
        if cancellation:
            diagnostic = {'content': complete_content} if getattr(cancellation, 'diagnostic', False) else {}
            cancellation.mark('brain_response', request_id=request_id, model=final.get('model'),
                done=final.get('done', False), done_reason=final.get('done_reason'),
                content_characters=len(complete_content), tool_count=len(calls),
                eval_count=final.get('eval_count'), prompt_eval_count=final.get('prompt_eval_count'), **diagnostic)
        _validate_completion(final, complete_content, calls)
        ended = perf_counter()
        if cancellation:
            cancellation.check()
        return LLMResponse(complete_content, calls, final), {
            "first_token_seconds": (first_event or ended) - started,
            "first_content_seconds": (first_content or ended) - started,
            "total_seconds": ended - started,
            "load_seconds": final.get("load_duration", 0) / 1e9,
            "eval_tokens_per_second": (
                final.get("eval_count", 0) / (final.get("eval_duration", 1) / 1e9)
                if final.get("eval_duration") else 0
            ),
        }
