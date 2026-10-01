"""Authenticated loopback lifecycle/turn transport for the one local runtime.

No shell, arbitrary function execution or unauthenticated HTTP routes. Requests
join the same voice command queue; final text is never logged by this transport.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import secrets
from threading import Thread
from urllib.request import Request, urlopen

import psutil


class RuntimeControl:
    @classmethod
    def background(cls, config, runtime, voice, shutdown):
        import logging
        from threading import Timer
        from time import monotonic
        from kk.core.state import AgentState

        from kk.core.desktop_service import DesktopService, PROTOCOL
        desktop = DesktopService(runtime.memory, voice.loop) if hasattr(runtime, "memory") else None

        from kk.core.desktop_settings import DesktopSettings, snapshot as settings_snapshot
        settings=DesktopSettings(config.root)
        loop_owner=getattr(voice.loop.agent,'orchestrator',None)
        from kk.core.web import WebService
        web=WebService(config.root,settings)
        models = None
        from kk.core.project_git import ProjectGit
        project_git=ProjectGit(settings)
        if desktop and getattr(runtime.provider,'name','') == 'ollama':
            from kk.core.model_management import ModelManager
            models = ModelManager(desktop,runtime.provider,settings,config)
            desktop.models = models
            runtime.provider.model_manager = models
            loop_owner.model_manager = models
        if loop_owner:
            loop_owner.desktop_settings=settings
            loop_owner.web=web
            if hasattr(loop_owner,'tools'):loop_owner.tools.internet_settings=settings

        def handle(request):
            operation = request.get('operation')
            loop = voice.loop
            if operation == 'hello':
                return dict(protocol=PROTOCOL, identity=server.metadata['identity'],
                            pid=os.getpid(), build=getattr(runtime,'build',{}),
                            capabilities=['conversations','request_snapshot','cancel','voice_control','voice_live','voice_live_background','dictation','chat_delete','chat_pin','archive','text_attachments','projects'] + (['models'] if models else []),
                            internet=settings.get('internet','off'),
                            voice_envelope=__import__('kk.ui.edge_model',fromlist=['voice_energy']).voice_energy(runtime.state.state.value,getattr(runtime.state,'audio_level',0),getattr(runtime.state,'tts_level',0)),
                            live_active=bool(getattr(loop,'desktop_live_scope',None) and getattr(loop,'conversation',None) and loop.conversation.active),
                            live_inline=runtime.state.desktop_inline_live,
                            voice_hotkey=getattr(loop,'hotkey',''),
                            text_ready=not loop._exit.is_set(), state=runtime.state.state.value,
                            **__import__('kk.core.desktop_readiness',fromlist=['snapshot']).snapshot(loop),
                            queue_depth=desktop.queue.qsize())
            if operation == 'settings.get':return settings_snapshot(config,loop,settings)
            if isinstance(operation,str) and operation.startswith('git.'):return project_git.handle(request)
            if operation == 'apps.list':
                from kk.core.desktop_apps import catalog
                return catalog()
            if operation == 'apps.open':
                from kk.core.desktop_apps import launch
                return launch(request['id'], settings)
            if isinstance(operation,str) and operation.startswith('models.'):
                if not models:raise RuntimeError('Model management requires the Ollama provider')
                return models.handle(request)
            if operation == 'memory.list':
                return [dict(r) for r in runtime.memory.connection.execute('SELECT key,value,category FROM memories ORDER BY updated_at DESC LIMIT 50')]
            if operation in ('memory.save','memory.forget'):
                # Serialize cache invalidation with the existing turn owner.
                from kk.tools.memory import _remember, _forget
                key=request.get('key');value=request.get('value')
                if not isinstance(key,str) or not key or len(key)>200:raise ValueError('Invalid memory key')
                with loop_owner._turn_lock:
                    if operation=='memory.forget':return _forget(runtime.memory,loop_owner.tools,key)
                    if not isinstance(value,str) or not value.strip() or len(value)>2000:raise ValueError('Invalid memory value')
                    old=runtime.memory.get(key)
                    if not old:raise ValueError('Unknown memory key')
                    return _remember(runtime.memory,key,value,old['category'],loop_owner.tools)
            if operation == 'settings.clearWebCache':return web.clear_cache()
            if operation == 'settings.save':
                result=settings.save(request.get('key'),request.get('value'))
                if request.get('key')=='internet' and request.get('value')=='off' and models:models.cancel_network()
                if request.get('key')=='observation_paused':runtime.context.pause(request['value'])
                return result
            if isinstance(operation,str) and operation.startswith('dictation.'):
                if operation=='dictation.start': desktop.validate_target(request.get('conversation_id'))
                return loop.dictation.handle(operation,request)
            if operation == 'voice.stopLive':
                from kk.voice.dictation import stop_live
                return stop_live(loop)
            if operation == 'voice.liveBackground':
                with loop.input_lock:
                    cid = getattr(loop, 'desktop_live_scope', None)
                    if (not cid or request.get('conversation_id') != cid
                            or not getattr(loop, 'conversation', None) or not loop.conversation.active):
                        raise RuntimeError('No active Onyx Live session for this conversation')
                    loop.state.set_desktop_inline_live(False)
                    loop.state.activate_visual('desktop_live_background')
                    return dict(live_active=True, background=True, conversation_id=cid)
            if isinstance(operation,str) and operation.startswith('desktop.'):
                return desktop.handle(request)
            if operation == 'voice.live':
                with loop.input_lock:
                    if not loop.capture or not loop.capture.ready.is_set() or not hasattr(loop,'conversation'):
                        raise RuntimeError('Voice capture is unavailable')
                    if loop.paused or loop.manual_recording or loop.manual_pending or loop.dictation.active or loop.agent.orchestrator.current_task:
                        raise RuntimeError('Voice is paused or busy')
                    cid=request.get('conversation_id')
                    if not desktop.db.execute('SELECT 1 FROM desktop_chats WHERE conversation_id=?',(cid,)).fetchone() and not __import__('re').fullmatch(r'draft:[0-9a-f]{32}',str(cid)):raise ValueError('Unknown conversation')
                    if loop.conversation.active and getattr(loop,'desktop_live_scope',None)!=cid:
                        raise RuntimeError('A voice conversation is already active; wait for it to finish')
                    if loop.conversation.active and getattr(loop,'desktop_live_scope',None)==cid:
                        return dict(started=True,conversation_id=cid)
                    loop.desktop_live_scope=cid
                    loop.state.set_desktop_inline_live(True)
                    loop.conversation.touch()
                    loop.wake_trigger()
                    return dict(started=bool(loop.conversation.active),conversation_id=cid)
            if operation in ('voice.start','voice.commit','voice.cancel'):
                with loop.input_lock:
                    if not loop.capture or not loop.capture.ready.is_set(): raise RuntimeError('Voice capture is unavailable')
                    if operation == 'voice.start':
                        if loop.paused or loop.manual_recording or loop.manual_pending: raise RuntimeError('Voice is paused or already recording')
                        cid = request.get('conversation_id')
                        if not desktop.db.execute('SELECT 1 FROM desktop_chats WHERE conversation_id=?',(cid,)).fetchone() and not __import__('re').fullmatch(r'draft:[0-9a-f]{32}',str(cid)): raise ValueError('Unknown conversation')
                        loop.desktop_voice_scope = cid
                        loop.mic_start()
                    elif operation == 'voice.commit': loop.mic_commit()
                    else:
                        loop.mic_cancel()
                        loop.desktop_voice_scope = None
                    return dict(recording=loop.manual_recording,pending=loop.manual_pending,
                                conversation_id=getattr(loop,'desktop_voice_scope',None))
            if operation == 'status':
                from kk.ui.capture_guard import diagnostics as capture_diagnostics
                manager = getattr(runtime.provider, 'resource_manager', None)
                tts = getattr(loop, 'tts', None)
                return dict(pid=os.getpid(), build=getattr(runtime, 'build', {}),
                            tts=(tts.runtime_diagnostics() if hasattr(tts, 'runtime_diagnostics') else {}),
                            renderer_capture=capture_diagnostics(),
                            state=runtime.state.state.value, microphone_paused=loop.paused,
                            capture_ready=bool(loop.capture and loop.capture.ready.is_set()),
                            voice_ready=bool(getattr(runtime.state, 'readiness', False)),
                            brain_model=runtime.model, manual_recording=loop.manual_recording,
                            user_visual_hidden=getattr(runtime.state, 'user_visual_hidden', False),
                            ambient_counters=(runtime.context.observation_counters()
                                if hasattr(runtime.context, 'observation_counters') else {}),
                            ambient_semantic=(runtime.context.semantic_diagnostics()
                                if hasattr(runtime.context, 'semantic_diagnostics') else {}),
                            task_id=getattr(runtime.agent.orchestrator.current_task, 'id', None),
                            resources=manager.snapshot() if manager else {},
                            models=getattr(manager, 'warm_states', {}))
            if operation == 'shutdown':
                logging.getLogger('kk').info('[CONTROL] orderly shutdown requested')
                Timer(.1, shutdown).start()
                return {'shutdown_requested': True}
            if operation == 'microphone':
                paused = request.get('paused')
                if not isinstance(paused, bool):
                    raise ValueError('paused must be boolean')
                # Serialize with manual-start/commit so capture cannot become
                # paused between the recording check and the state transition.
                with loop.input_lock:
                    if loop.manual_recording or loop.manual_pending:
                        raise RuntimeError('Commit or cancel the current recording first')
                    loop.paused = paused
                    if paused:
                        loop.interrupt(reason='microphone_paused', source='local_control')
                        if loop.capture: loop.capture.paused.set()
                        if loop.audio_focus: loop.audio_focus.release()
                    else:
                        runtime.policy.clear_stop()
                        if loop.capture: loop.capture.paused.clear()
                    runtime.state.set(AgentState.PAUSED if paused else AgentState.IDLE)
                return {'paused': paused}
            if operation == 'ask':
                if not loop.capture or not loop.capture.ready.is_set():
                    raise RuntimeError('Runtime voice initialization has not completed')
                started = monotonic()
                token = loop.dispatch_text(request.get('text'), spoken=bool(request.get('spoken', False)),
                                           diagnostic=bool(request.get('diagnostic', False)))
                if not token.completed.wait(runtime.agent.orchestrator.timeout_seconds + 30):
                    token.cancel(reason='timeout', source='local_control')
                    raise TimeoutError('Runtime turn completion timed out')
                # The command worker publishes this snapshot before completed.
                # A later fast turn may already have replaced last_task by now.
                from copy import deepcopy
                snapshot = deepcopy(getattr(token, 'result_snapshot', {}))
                result = dict(task_id=token.trace_id,
                              answer=snapshot.get('answer', getattr(token, 'answer', None)),
                              error=snapshot.get('error', getattr(token, 'error', None)),
                              cancelled=snapshot.get('cancelled', token.event.is_set()),
                              seconds=monotonic()-started,
                              status=snapshot.get('status', 'CANCELLED' if token.event.is_set() else 'UNKNOWN'),
                              metrics=snapshot.get('metrics', {}))
                if request.get('diagnostic'):
                    result['trace'] = snapshot.get('trace', list(token.trace) + list(token.speech_token.trace))
                    result['actions'] = snapshot.get('actions', [])
                return result
            raise ValueError('Supported operations: status, shutdown, microphone, ask')

        server = cls(config.root, handle, getattr(runtime, 'build', {}))
        server.desktop = desktop
        return server

    def __init__(self, root, handler, build=None):
        self.path = Path(root) / 'data' / 'runtime_control.json'
        self.handler = handler
        self.secret = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != '/control' or not hmac.compare_digest(
                        self.headers.get('Authorization', ''), 'Bearer ' + owner.secret):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 65536:
                        raise ValueError('Invalid request size')
                    self.connection.settimeout(5)
                    request = json.loads(self.rfile.read(length))
                    if not isinstance(request, dict):
                        raise ValueError('Expected JSON object')
                    result = owner.handler(request)
                    payload = json.dumps({'ok': True, 'data': result}, ensure_ascii=False, default=str).encode('utf-8')
                except Exception as exc:
                    payload = json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.metadata = dict(pid=os.getpid(), created=psutil.Process().create_time(),
                             port=self.server.server_port, token=self.secret, build=build or {},
                             protocol=1, identity=secrets.token_hex(16))
        self.thread = Thread(target=self.server.serve_forever, name='OnyxLocalControl', daemon=True)

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.metadata), encoding='utf-8')
        temporary.replace(self.path)
        self.thread.start()

    def close(self):
        if getattr(self,'desktop',None) and getattr(self.desktop,'models',None):
            self.desktop.models.close()
        if getattr(self,"desktop",None): self.desktop.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if saved.get('token') == self.secret:
                self.path.unlink()
        except (FileNotFoundError, ValueError):
            pass


def request_control(root, operation, timeout=10, **arguments):
    path = Path(root) / 'data' / 'runtime_control.json'
    metadata = json.loads(path.read_text(encoding='utf-8'))
    process = psutil.Process(metadata['pid'])
    if abs(process.create_time() - metadata['created']) > .01:
        raise RuntimeError('Stale runtime identity')
    request = Request(f"http://127.0.0.1:{int(metadata['port'])}/control",
                      data=json.dumps(dict(operation=operation, **arguments), ensure_ascii=False).encode('utf-8'),
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + metadata['token']},
                      method='POST')
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError(result.get('error', 'Runtime request failed'))
    return result['data']
