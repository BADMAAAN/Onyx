from __future__ import annotations

from threading import Event, Thread, RLock
from functools import wraps
from queue import Queue, Empty
from time import perf_counter

import keyboard
import logging

from kk.core.agent import Agent
from kk.core.state import AgentState, StateMachine
from kk.permissions.policy import PermissionPolicy
from kk.performance import PerformanceRecorder

from .stt_faster_whisper import FasterWhisperSTT
from .tts_silero import SileroTTS
from .speech_queue import SpeechQueue, SentenceBuffer
from kk.core.task import CancellationToken


STOP_PHRASES = {"стоп", "замолчи", "прекрати", "остановись", "кей кей стоп", "хватит", "отмена", "stop", "cancel"}


def input_control(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.input_lock:
            return method(self, *args, **kwargs)
    return guarded


def is_stop(text):
    from kk.core.fast_commands import normalize
    return normalize(text).casefold().strip(' ,.!?') in STOP_PHRASES


class VoiceLoop:
    def __init__(self, agent: Agent, stt: FasterWhisperSTT, tts: SileroTTS, state: StateMachine, policy: PermissionPolicy, hotkey: str = "ctrl+space", performance: PerformanceRecorder | None = None):
        self.agent = agent
        self.stt = stt
        self.tts = tts
        self.state = state
        self.policy = policy
        self.hotkey = hotkey
        self.performance = performance
        self._trigger = Event()
        self._exit = Event()
        self.input_lock = RLock()
        self.stop_lock = RLock()
        self._stop_started = False
        self.paused = False
        self.speech = SpeechQueue(tts, state)
        self.worker = None
        self.commands = Queue(maxsize=1)
        self.generation = None
        self.wakeword = False
        self.wake_phrase = 'оникс'
        self.record_options = {}
        self.native_wakeword = None
        self.barge_in_mode = 'speech'
        self.streaming_options = {}
        self.conversation_timeout = 25
        self.audio_focus = None
        self.echo = None
        self.startup_phase="starting";self.startup_error=""
        self.stt_ready=self.tts_ready=self.brain_ready=False
        self.capture = None
        self.decoder = None
        self.last_transcription = None
        self.last_voice_sample = None
        self.manual_recording = False
        self.manual_pending = False
        self.manual_previous_state = AgentState.IDLE
        self.manual_maximum = 300
        from .dictation import Dictation
        self.dictation = Dictation(self)
        from .recognition import Clarification, RecognitionRecovery
        self.clarification = Clarification()
        self.voice_recovery = RecognitionRecovery()
        self.voice_notice = ''

    def _wait_native(self):
        import sounddevice as sd
        from collections import deque
        from .audio_devices import find_device
        self.native_wakeword.reset()
        frames = deque(maxlen=25)
        from .audio_input import AudioInput
        with AudioInput(find_device(self.stt.microphone,input_device=True),1280) as stream:
            while not self._exit.is_set() and not self.paused:
                if self._trigger.is_set():
                    self._trigger.clear()
                    return None
                pcm, overflow = stream.read(1280)
                if overflow:
                    continue
                pcm = bytes(pcm)
                frames.append(pcm)
                if self.native_wakeword.process(pcm)['activated']:
                    self.state.set(AgentState.LISTENING, 'Слушаю…')
                    return b''.join(frames)
        raise InterruptedError('Wake listening cancelled')

    def trigger(self) -> None:
        self.mic_toggle()

    def mic_toggle(self):
        if self.paused:
            self.paused=False
            if self.capture:self.capture.paused.clear()
            self.policy.clear_stop()
            self.state.set(AgentState.IDLE)
            return
        if self.manual_recording:
            self.mic_commit()
        else:
            self.mic_start()

    @input_control
    def mic_start(self):
        if self.paused or not self.capture or self.manual_recording or self.manual_pending:
            return
        self.manual_previous_state = self.state.state
        self.manual_recording = True
        self.voice_recovery.reset()
        self.voice_notice = ''
        self.state.activate_visual('manual')
        if hasattr(self,'conversation') and not self.dictation.active: self.conversation.touch()
        if self.decoder: self.decoder.invalidate()
        self.clarification.pending = None
        self.interrupt_speech()
        if self.audio_focus: self.audio_focus.acquire()
        self.state.set(AgentState.RECORDING)
        self.capture.controls.put('start')

    @input_control
    def mic_commit(self):
        if self.manual_recording and self.capture:
            self.manual_recording = False
            self.manual_pending = True
            self.state.set(AgentState.COMMITTING)
            self.capture.controls.put('commit')

    @input_control
    def mic_cancel(self):
        if (self.manual_recording or self.manual_pending) and self.capture:
            self.manual_recording = self.manual_pending = False
            self.desktop_voice_scope = None
            if self.decoder: self.decoder.invalidate()
            self.capture.controls.put('cancel')
            self.clarification.pending = None
            self.interrupt_speech()
            if self.audio_focus: self.audio_focus.release()
            previous = self.manual_previous_state
            self.state.set(previous if previous in {AgentState.IDLE, AgentState.SLEEPING, AgentState.MIC_READY} else AgentState.IDLE)

    @input_control
    def wake_trigger(self) -> None:
        if self.paused:
            return
        self.interrupt_speech()
        self.voice_recovery.reset()
        self.voice_notice = ''
        self.state.activate_visual('hotkey')
        self._trigger.set()

    def interrupt(self, reason='user_stop', source='voice_control'):
        if self.generation and not self.generation.completed.is_set():
            self.generation.cancel(reason=reason, source=source)
        self.agent.cancel()
        self.speech.cancel(reason=reason, source=source)

    def interrupt_speech(self, reason='user_stop', source='voice_control'):
        """Stop audio only after explicit control or confirmed non-echo language."""
        if reason == 'user_stop' and source == 'voice_control':
            self.speech.cancel()  # legacy callers/tests; queue still records a reason
        else:
            self.speech.cancel(reason=reason, source=source)

    def save_last_voice_sample(self):
        return self.last_voice_sample.save() if self.last_voice_sample else None

    def _own_voice_cue(self, token):
        token.source = 'voice'
        token.session_id = (getattr(self, 'desktop_live_scope', None)
                            or getattr(self, 'desktop_turn_scope', None)
                            or getattr(self, 'desktop_voice_scope', None) or 'legacy-voice')
        return token

    @input_control
    def _reject_final(self, result):
        # A stop-Live/cancel request may have invalidated this event since it
        # left the capture queue. Never revive audio for that expired session.
        if (not self.conversation.active or (result.capture_epoch is not None
                and self.decoder and result.capture_epoch != self.decoder.epoch)):
            return
        # The short prompt asks for a complete new utterance. Do not retain an
        # unspoken low-confidence command that a later "yes" could authorize.
        self.clarification.pending = None
        self.voice_notice = 'Не расслышала. Попробуй ещё раз.'
        self.state.set(AgentState.LISTENING, self.voice_notice)
        token = self._own_voice_cue(CancellationToken(diagnostic=bool(getattr(self, 'diagnostic', False))))
        token.trace_id = getattr(result, 'turn_id', None) or getattr(result, 'trace_id', None) or token.generation_id
        token.turn_id = token.trace_id
        speak = self.voice_recovery.reject() and bool(result.text)
        token.mark('stt_rejected', final_transcript=result.text,
            raw_transcript=result.raw_transcript, reason=result.rejection_reason,
            audio_id=getattr(result, 'audio_id', None), spoken_prompt=speak,
            answer=self.voice_notice, committed=getattr(result, 'committed', False))
        self.last_rejection_token = token
        self.desktop_voice_scope = self.desktop_turn_scope = None
        if speak:
            # No recognition during this short error cue, including synthesis,
            # drain and the room tail. This is not the normal answer policy.
            token.suppress_capture = True
            if self.decoder:
                self.decoder.invalidate()
            self.speech.begin(token)
            self.speech.submit(self.voice_notice, token)
            self.speech.finish(token)

    def emergency_stop(self):
        self.interrupt()
        self.policy.trigger_stop()
        self.stt.cancel()
        self.paused = True
        if self.audio_focus: self.audio_focus.release()
        self.state.set(AgentState.STOPPED, 'Emergency Stop; resume explicitly from tray')

    def stop(self) -> None:
        # Tray/coordinator and run.finally can arrive concurrently. In particular,
        # sounddevice's convenience sd.stop() must not close the same native ptr twice.
        with self.stop_lock:
            if self._stop_started:
                return
            self._stop_started = True
            self._exit.set()
            self.state.set_desktop_inline_live(False)
            self.state.set_readiness(False)
            if self.audio_focus: self.audio_focus.release()
            self.interrupt(reason='shutdown', source='voice_stop')
            self.stt.cancel()
            self.tts.stop()
            self.state.set(AgentState.STOPPED)
            self._trigger.set()

    def _complete_turn(self, token):
        """Publish an immutable-per-turn result before waking IPC waiters."""
        if token.completed.is_set(): return
        from copy import deepcopy
        task_id = getattr(token, 'trace_id', token.generation_id)
        orchestrator = getattr(self.agent, 'orchestrator', None)
        task = getattr(orchestrator, 'last_task', None)
        if getattr(task, 'id', None) != task_id: task = None
        cancelled = token.event.is_set()
        error = getattr(token, 'error', None)
        status = ('CANCELLED' if cancelled else 'FAILED' if error else
                  task.status.value if task is not None else 'UNKNOWN')
        token.mark('voice_answer_end', cancelled=cancelled,
                   cancellation_reason=token.cancel_reason,
                   speech_cancelled=getattr(token, 'speech_token', token).event.is_set())
        token.result_snapshot = dict(task_id=task_id, answer=getattr(token, 'answer', None),
            error=error, cancelled=cancelled, status=status,
            metrics=deepcopy(task.metrics) if task is not None else {})
        if token.diagnostic:
            token.result_snapshot['actions'] = deepcopy(task.actions) if task is not None else []
            speech = getattr(token, 'speech_token', token)
            trace = list(token.trace) + (list(speech.trace) if speech is not token else [])
            if hasattr(token, 'failure_speech_token'):
                trace += list(token.failure_speech_token.trace)
            token.result_snapshot['trace'] = deepcopy(sorted(trace, key=lambda row:row.get('monotonic', 0)))
        if hasattr(token, "on_complete"):
            token.on_complete()
        token.completed.set()

    def _answer(self, text, token):
        import uiautomation as auto
        with auto.UIAutomationInitializerInThread():
            try:
                speech_token = getattr(token, 'speech_token', token)
                def speak(chunk):
                    speech_token = getattr(token, 'speech_token', token)
                    if not speech_token.event.is_set():
                        try: self.speech.submit(chunk, speech_token)
                        except InterruptedError: pass
                buffer = SentenceBuffer(speak)
                def deliver(chunk):
                    if hasattr(token, "on_content"): token.on_content(chunk)
                    buffer.feed(chunk)
                token.answer = self.agent.ask(text, on_token=deliver, cancellation=token)
                token.mark('full_answer', answer=token.answer)
                token.check()
                buffer.flush()
                speech_token = getattr(token, 'speech_token', token)
                if self.speech.error is not None and speech_token is self.speech.token:
                    raise self.speech.error
                if not speech_token.event.is_set():
                    self.speech.finish(speech_token)
                    self.speech.wait(speech_token)
                if not token.event.is_set():
                    active = bool(getattr(getattr(self, 'conversation', None), 'active', False))
                    self.state.set(AgentState.PAUSED if getattr(self, 'paused', False) else AgentState.LISTENING if active else AgentState.IDLE,
                                   'Слушаю…' if active and not getattr(self, 'paused', False) else '')
            except InterruptedError:
                pass
            except Exception as exc:
                token.error = f'{type(exc).__name__}: {exc}'
                logging.getLogger('kk').exception('Voice answer failed task_id=%s response_id=%s',
                    getattr(token, 'trace_id', token.generation_id), token.response_id)
                token.mark('worker_error', source='voice_answer', error_type=type(exc).__name__)
                if not token.event.is_set():
                    self.speech.cancel(reason='worker_error', source='voice_answer')
                    if self.audio_focus: self.audio_focus.release()
                    self.state.set(AgentState.ERROR, str(exc)[:90])
                    # Backend/perception errors must not become silent voice turns.
                    from kk.permissions.policy import EmergencyStop
                    if not isinstance(exc, EmergencyStop) and not self.paused:
                        failure = token.speech_child()
                        failure.silent = bool(getattr(speech_token, 'silent', False))
                        failure = self.speech.begin(failure)
                        token.failure_speech_token = failure
                        try:
                            from kk.providers.resources import ResourcePressure
                            detail = ('Недостаточно свободной памяти для локальной модели.' if isinstance(exc, ResourcePressure)
                                      else 'Локальная модель не ответила вовремя.' if isinstance(exc, TimeoutError)
                                      else 'Ошибка обработки; подробности в журнале Onyx.')
                            self.speech.submit('Не удалось завершить запрос. '+detail, failure)
                            self.speech.finish(failure)
                            self.speech.wait(failure)
                        except InterruptedError:
                            pass
                        except Exception:
                            logging.getLogger('kk').exception('Failure announcement playback failed task_id=%s', token.trace_id)
                        self.state.set(AgentState.ERROR, str(exc)[:90])
            finally:
                self._complete_turn(token)

    @input_control
    def dispatch(self, text, evidence=None):
        if evidence is None or not getattr(evidence, 'committed', False) or not evidence.accepted:
            return False
        if evidence.capture_epoch is not None and self.decoder and evidence.capture_epoch != self.decoder.epoch:
            return False
        service = getattr(self, "desktop_service", None)
        if service:
            cid = (getattr(self, "desktop_voice_scope", None) or getattr(self, "desktop_turn_scope", None)
                   or getattr(self, "desktop_live_scope", None) or "legacy-voice")
            self.desktop_voice_scope = None
            self.desktop_turn_scope = None
            if service.is_deleted(cid): return False
            service.create("Voice", cid)
            rid = getattr(evidence, "trace_id", None) or __import__("uuid").uuid4().hex
            return service.submit(cid, rid, text, source="voice", spoken=True, evidence=evidence)
        return self._enqueue(text, evidence=evidence)

    @input_control
    def dispatch_text(self, text, *, spoken=False, diagnostic=False):
        """Explicit committed local text input, sharing the production turn queue."""
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError('Expected a nonempty text request up to 16000 characters')
        if getattr(self, '_exit', None) is not None and self._exit.is_set():
            raise RuntimeError('Runtime is shutting down')
        if self.manual_recording or self.manual_pending:
            raise RuntimeError('Manual recording is active; commit or cancel it first')
        if self.agent.orchestrator.current_task or (self.generation and not self.generation.completed.is_set()):
            raise RuntimeError('A task is already active; text transport will not interrupt it')
        token = self._enqueue(text, diagnostic=diagnostic, silent=not spoken)
        return token

    def _enqueue(self, text, evidence=None, diagnostic=False, silent=False, token=None):
        # A final new intent supersedes the old generation, not just its audio.
        # Otherwise the old worker can finish later and revive stale speech/state.
        if (self.generation and not self.generation.completed.is_set()
                and not self.generation.event.is_set()):
            self.generation.cancel(reason='superseded', source='final_transcript')
        active = self.agent.orchestrator.current_task
        if active and getattr(active, 'cancellation_token', None):
            active.cancellation_token.cancel(reason='superseded', source='final_transcript')
        self.interrupt_speech(reason='superseded', source='final_transcript')
        token = token or CancellationToken(diagnostic=diagnostic or bool(getattr(self, 'diagnostic', False)))
        token.trace_id = getattr(token,'trace_id',None) or getattr(evidence,'trace_id',None) or token.generation_id
        token.turn_id = token.trace_id
        token.voice_evidence = evidence
        token.mark('voice_final_transcript' if evidence else 'text_committed', accepted=True,
                   committed=True, capture_epoch=getattr(evidence, 'capture_epoch', None),
                   decode_seconds=getattr(evidence, 'latency_seconds', None),
                   text=getattr(evidence, 'text', text),
                   raw_transcript=getattr(evidence, 'raw_transcript', text),
                   final_transcript=getattr(evidence, 'text', text), request=text)
        token.speech_token = token.speech_child()
        token.speech_token.silent = silent
        self.generation = token
        self.speech.begin(token.speech_token)
        try:
            old_text,old_token=self.commands.get_nowait()
            old_token.cancel(reason='superseded', source='pending_turn')
            self._complete_turn(old_token)
        except Empty:
            pass
        self.commands.put_nowait((text,token))
        if not self.worker or not self.worker.is_alive():
            self.worker = Thread(target=self._commands, name='KKCommand', daemon=True)
            self.worker.start()
        return token

    def _commands(self):
        while not self._exit.is_set():
            try:
                text,token=self.commands.get(timeout=.1)
            except Empty:
                continue
            if token.event.is_set():
                self._complete_turn(token)
                continue
            if not token.event.is_set():
                try:
                    self._answer(text, token)
                except Exception as exc:
                    token.error = f'{type(exc).__name__}: {exc}'
                    logging.getLogger('kk').exception('Conversation worker failed task_id=%s', token.trace_id)
                    token.mark('worker_error', source='conversation_worker', error_type=type(exc).__name__)
                    self.speech.cancel(reason='worker_error', source='conversation_worker')
                    self.state.set(AgentState.ERROR, str(exc)[:90])
                    self._complete_turn(token)

    def _watch_stop(self):
        while not self._exit.wait(.05):
            if self.policy.stop_file.exists() and not self.paused:
                self.emergency_stop()

    def run(self) -> None:
        import logging
        logger = logging.getLogger('kk')
        hotkeys = [keyboard.add_hotkey(self.hotkey, self.trigger, suppress=False),
                   keyboard.add_hotkey('esc', self.mic_cancel, suppress=False),
                   keyboard.add_hotkey('ctrl+alt+pause', self.emergency_stop, suppress=False)]
        Thread(target=self._watch_stop, name='KKKillSwitch', daemon=True).start()
        try:
            self.state.set(AgentState.THINKING,'Загружаю распознавание')
            for attempt in range(3):
                try:
                    started = perf_counter()
                    self.startup_phase="STT"
                    self.stt.prewarm()
                    self.stt_ready=True
                    if self.performance: self.performance.record('startup_stt',perf_counter()-started)
                    self.state.set(AgentState.THINKING,'Подготавливаю голос')
                    started = perf_counter()
                    self.startup_phase="TTS"
                    self.tts.prewarm()
                    self.tts_ready=True
                    if self.performance: self.performance.record('startup_tts',perf_counter()-started,voice=self.tts.actual_voice)
                    break
                except Exception as exc:
                    logger.exception('Voice model prewarm failed (attempt %s/3)',attempt+1)
                    self.state.set(AgentState.ERROR,'Voice models: '+str(exc)[:100])
                    if attempt == 2:
                        self.startup_error=type(exc).__name__+": "+str(exc)[:180]
                        return
                    self._exit.wait(2)
            if self._exit.is_set():
                return
            # Do not report ready until the selected input/output endpoints open.
            import numpy as np
            import sounddevice as sd
            from .audio_devices import find_device
            from .audio_input import AudioInput
            self.state.set(AgentState.THINKING,'Проверяю микрофон и звук')
            for attempt in range(3):
                try:
                    with AudioInput(find_device(self.stt.microphone,input_device=True),480) as stream:
                        deadline=perf_counter()+3
                        while not self._exit.is_set():
                            pcm,overflow=stream.read(480)
                            if pcm and not overflow:
                                logger.info('[AUDIO] input=%s rate=%s selected_channel=%s rms=%s peak=%.4f',
                                    stream.device_name,stream.native_rate,stream.selected_channel,
                                    [round(x,5) for x in stream.channel_rms],stream.peak)
                                break
                            if perf_counter()>deadline:
                                raise RuntimeError('No healthy microphone packets')
                    if self._exit.is_set():
                        return
                    sd.play(np.zeros(2400,dtype=np.float32),self.tts.sample_rate,
                        device=find_device(self.tts.output_device,input_device=False),blocking=True)
                    break
                except Exception as exc:
                    logger.exception('Audio prewarm failed (attempt %s/3)',attempt+1)
                    self.state.set(AgentState.ERROR,'Audio prewarm: '+str(exc)[:100])
                    if attempt == 2:
                        self.startup_error=type(exc).__name__+": "+str(exc)[:180]
                        return
                    self._exit.wait(1)
            if self._exit.is_set():
                return
            brain=self.agent.orchestrator
            self.state.set(AgentState.THINKING,'Загружаю модель')
            try:
                brain.tools.app_resolver.refresh()
            except Exception as exc:
                import logging
                logging.getLogger('kk').warning('App index prewarm: %s', exc)
            # Exact desktop intents remain available when the LLM service is off.
            # Arbitrary reasoning and Vision still report their actual backend error.
            self.startup_phase="Brain"
            brain_ready = False
            if brain.provider.is_available():
                try:
                    started = perf_counter()
                    if hasattr(brain.provider,'chat_stream'):
                        response,timings=brain.provider.chat_stream(brain.model,
                            [{'role':'user','content':'Ответь одним словом: готов'}],temperature=0)
                        if not response.content.strip(): raise RuntimeError('Brain returned no answer')
                        if self.performance:
                            self.performance.record('startup_brain_load',timings.get('load_seconds',0),model=brain.model)
                            self.performance.record('startup_brain_first_response',timings.get('total_seconds',0),model=brain.model)
                    else:
                        if not brain.provider.check_inference(brain.model): raise RuntimeError('Brain returned no answer')
                    # Probe tool selection without executing the suggested call.
                    probe={'type':'function','function':{'name':'onyx_readiness_probe',
                        'description':'Проверка выбора инструмента без выполнения действия',
                        'parameters':{'type':'object','properties':{}}}}
                    call=brain.provider.chat(brain.model,
                        [{'role':'user','content':'Вызови инструмент onyx_readiness_probe.'}],tools=[probe])
                    if not any(item.name=='onyx_readiness_probe' for item in call.tool_calls):
                        raise RuntimeError('Brain did not select a tool')
                    brain_ready = True
                    if self.performance: self.performance.record('startup_brain',perf_counter()-started,model=brain.model)
                except Exception as exc:
                    logger.exception('Brain prewarm failed')
                    self.state.set(AgentState.ERROR, 'Brain prewarm: '+str(exc)[:70])
            else:
                logger.warning('Brain unavailable at startup; exact desktop commands can still run')
            self.brain_ready=brain_ready
            self.startup_phase="capture"
            self._streaming_session(brain_ready)
        finally:
            self.stop()
            for hook in hotkeys:
                keyboard.remove_hotkey(hook)
            self.speech.close()
            if self.worker:
                self.worker.join(timeout=3)

    def _streaming_session(self, brain_ready=True):
        from .streaming import (TurnManager, VAD, StreamingSTT, AudioCapture,
                                ConversationManager, BargeInController)
        from .wakeword import STTWakeWordProvider
        from queue import Full
        events = Queue(128)
        event_times = {}
        def emit(kind, sequence, data):
            if kind == 'native_wake': event_times[kind] = perf_counter()
            try: events.put_nowait((kind, sequence, data))
            except Full:
                if kind != 'level':
                    self.interrupt_speech()
                    self.state.set(AgentState.ERROR, 'Voice event overflow; repeat command')
        turn = TurnManager(**self.streaming_options)
        turn.manual_maximum = self.manual_maximum
        decoder = StreamingSTT(self.stt, emit)
        self.decoder = decoder
        conversation = ConversationManager(self.conversation_timeout, self.state)
        self.conversation = conversation
        self.state.subscribe(conversation.transition)
        closing_token = None
        detector = STTWakeWordProvider(self.wake_phrase)
        barge = BargeInController(lambda: self.interrupt_speech(
            reason='confirmed_barge_in', source='recognized_microphone_speech'), self.performance)
        self.barge = barge
        def on_speech():
            decoder.invalidate()
            if (conversation.active or self.speech.playing.is_set()) and self.barge_in_mode == 'speech':
                barge.speech_started(self.generation)
        self.capture = AudioCapture(self.stt.microphone, turn,
            VAD(self.record_options.get('silence_threshold', .0015)), decoder,
            emit, on_speech, self.echo, self.native_wakeword if self.wakeword else None)
        self.capture.enabled = lambda: self.wakeword or conversation.active or self.manual_recording or self.manual_pending
        self.capture.echo_gate = self.speech.is_echo
        self.capture.suppress_input = self.speech.capture_suppressed
        self.capture.start()
        if not self.capture.ready.wait(5):
            self.state.set_readiness(False)
            detail = self.capture.last_error or 'Microphone did not deliver audio packets'
            self.startup_error=detail
            self.state.set(AgentState.ERROR,'Capture: '+detail[:100])
            import logging
            logging.getLogger('kk').error('Voice capture startup failed: %s',detail)
            self.capture.close()
            decoder.close()
            return
        self.startup_phase="ready"
        self.state.set_readiness(True)
        if not self.paused:
            if brain_ready:
                self.state.set(AgentState.IDLE,'Ожидаю Оникс' if self.wakeword else f'Нажмите {self.hotkey}')
            else:
                self.state.set(AgentState.ERROR,'Частичная готовность: голос работает, модель недоступна')
            import logging
            logging.getLogger('kk').info('[VOICE] capture active; STT=%s TTS=%s brain_ready=%s',
                getattr(self.stt,'model_name','unknown'),getattr(self.tts,'actual_voice','unknown'),brain_ready)
        paused_before = False
        try:
            while not self._exit.is_set():
                barge.expire()
                if closing_token and (closing_token.event.is_set() or self.speech.done.is_set()):
                    if not closing_token.event.is_set():
                        conversation.active = False
                        if self.audio_focus: self.audio_focus.release()
                        self.state.set(AgentState.SLEEPING)
                    closing_token = None
                if self.paused:
                    self.capture.paused.set()
                    if not paused_before:
                        self.mic_cancel()
                        decoder.invalidate()
                        self.interrupt()
                        conversation.active = False
                        if self.audio_focus: self.audio_focus.release()
                    paused_before = True
                    self._exit.wait(.05)
                    continue
                paused_before = False
                self.capture.paused.clear()
                if self.stt.cancel_event.is_set():
                    self.stt.cancel_event.clear()
                    decoder.invalidate()
                    conversation.active = False
                    turn.reset()
                    if self.audio_focus: self.audio_focus.release()
                with self.input_lock:
                    if self._trigger.is_set():
                        self._trigger.clear()
                        decoder.invalidate()
                        conversation.touch()
                        if self.audio_focus: self.audio_focus.acquire()
                        self.state.set(AgentState.LISTENING, 'Слушаю…')
                busy = bool(self.agent.orchestrator.current_task) or self.manual_recording or self.manual_pending or turn.active or decoder.busy.is_set() or not decoder.finals.empty() or (self.generation and not self.generation.event.is_set()
                    and not self.speech.done.is_set())
                if conversation.expire(busy=busy):
                    self.desktop_live_scope = None
                    if self.audio_focus: self.audio_focus.release()
                    self.state.set(AgentState.SLEEPING)
                try: kind, sequence, data = events.get(timeout=.05)
                except Empty: continue
                if kind == 'level':
                    self.state.audio_level = data
                    ref = self.speech.render_reference(480)
                    import numpy as np
                    self.state.tts_level = float(np.sqrt(np.mean(ref*ref)))
                    continue
                if kind == 'manual_started':
                    if not self.dictation.capture_event(kind,sequence):conversation.touch()
                    continue
                if kind == 'manual_committing':
                    self.state.set(AgentState.COMMITTING)
                    continue
                if kind == 'manual_cancelled':
                    if self.dictation.capture_event(kind,sequence):continue
                    if sequence != turn.sequence: continue
                    self.manual_recording = self.manual_pending = False
                    conversation.active = False
                    if self.audio_focus: self.audio_focus.release()
                    previous = self.manual_previous_state
                    self.state.set(previous if previous in {AgentState.IDLE, AgentState.SLEEPING, AgentState.MIC_READY} else AgentState.IDLE)
                    continue
                if kind == 'native_wake':
                    if self.dictation.active: continue
                    if not conversation.active:
                        self.voice_recovery.reset()
                        self.voice_notice = ''
                        self.state.activate_visual('wake')
                        conversation.touch()
                        if self.audio_focus: self.audio_focus.acquire()
                        self.state.set(AgentState.LISTENING, 'Слушаю…')
                        if self.performance: self.performance.record('wake_visual_ack', perf_counter()-event_times.get(kind,perf_counter()), basis='native event queue to visual state; acoustic delay excluded')
                    continue
                if kind == 'speech_start':
                    self.desktop_turn_scope = getattr(self,'desktop_live_scope',None)
                    if self.manual_recording or sequence != turn.sequence: continue
                    if conversation.active:
                        conversation.touch()
                        self.state.set(AgentState.USER_SPEAKING)
                    continue
                if kind in ('error', 'discard'):
                    if self.dictation.consume(kind): continue
                    barge.reject(reason=kind)
                    self.manual_recording = self.manual_pending = False
                    turn.reset()
                    conversation.active = False
                    self.interrupt_speech()
                    if self.audio_focus: self.audio_focus.release()
                    self.state.set(AgentState.ERROR, str(data)[:120])
                    continue
                if kind not in ('partial', 'final'): continue
                # Any newer speech makes this hypothesis obsolete.
                if sequence != turn.sequence: continue
                if data['epoch'] != decoder.epoch: continue
                if not self.capture.controls.empty(): continue
                result = data['result']
                result.capture_epoch = data['epoch']
                if kind == 'final' and not self.voice_recovery.claim(result): continue
                if self.dictation.consume(kind, result): continue
                echo_text = (not self.manual_recording and not self.manual_pending
                    and self.speech.matches_recent_text(result.text) is True)
                if echo_text:
                    barge.reject('echo_transcript')
                    # Echo hypotheses must update endpointing but never conversation/tools.
                    if kind == 'partial':
                        turn.hypothesis(sequence, result.text, data['samples'])
                    continue
                if kind == 'final' and result.accepted and result.text:
                    self.voice_recovery.reset()
                    self.voice_notice = ''
                if result.accepted and (conversation.active or self.speech.playing.is_set()):
                    barge.confirm(result.text, final=kind == 'final')
                elif kind == 'final':
                    barge.reject('rejected_final')
                if kind == 'partial':
                    turn.hypothesis(sequence, result.text, data['samples'])
                    if self.manual_recording:
                        # Exact cancel is a control gesture, never an executable partial.
                        if result.accepted and result.text.casefold().strip(' .!?') in {'отмени запись', 'отмена записи'}:
                            self.mic_cancel()
                        continue
                    if self.wakeword and detector.detect_text(result.text) and not conversation.active:
                        self.state.activate_visual('wake')
                        conversation.touch()
                        if self.audio_focus: self.audio_focus.acquire()
                    if conversation.active:
                        self.state.set(AgentState.USER_SPEAKING, result.text)
                    continue
                if self.manual_pending and result.accepted and result.text.casefold().strip(' .!?') in {'отмена', 'отмени запись', 'отмена записи', 'cancel'}:
                    self.mic_cancel()
                    continue
                from .diagnostics import SAVE_COMMANDS
                from kk.core.fast_commands import normalize
                if (result.accepted and getattr(result, 'committed', False)
                        and (conversation.active or detector.detect_text(result.text))
                        and normalize(detector.command(result.text) if detector.detect_text(result.text)
                                      else result.text).casefold() in SAVE_COMMANDS):
                    saved = self.save_last_voice_sample()
                    token = self.speech.begin(self._own_voice_cue(CancellationToken()))
                    self.generation = token
                    self.speech.submit('Образец сохранён.' if saved else 'Последняя реплика уже недоступна.', token)
                    self.speech.finish(token)
                    continue
                if self.last_voice_sample and data.get('pcm'):
                    from .diagnostics import infer_intent
                    self.last_voice_sample.remember(data['pcm'], result,
                        infer_intent(result.text, result.accepted))
                self.last_transcription = result
                self.manual_pending = False
                if not result.text:
                    self.desktop_voice_scope = None
                    barge.reject('empty_final')
                    if not result.accepted and conversation.active:
                        self._reject_final(result)
                        continue
                    conversation.active = False
                    if self.audio_focus: self.audio_focus.release()
                    self.state.set(AgentState.IDLE)
                    continue
                woke = detector.detect_text(result.text)
                if not conversation.active:
                    if not self.wakeword or not woke or not result.accepted: continue
                    self.state.activate_visual('wake')
                    conversation.touch()
                    if self.audio_focus: self.audio_focus.acquire()
                command = detector.command(result.text) if woke else result.text
                from .dictation import is_stop_live, stop_live
                if result.accepted and getattr(result,'committed',False) and is_stop_live(command):
                    stop_live(self)
                    continue
                if result.accepted and getattr(result,'committed',False) and command.casefold().strip(' .!?') in ('выключи микрофон','turn off microphone'):
                    stop_live(self)
                    self.paused=True
                    self.capture.paused.set()
                    self.state.set(AgentState.PAUSED)
                    continue
                if result.accepted and getattr(result,'committed',False) and is_stop(command):
                    self.emergency_stop()
                    continue
                if result.accepted and getattr(result,'committed',False) and conversation.closing(command):
                    self.interrupt_speech()
                    closing_token = self.speech.begin(self._own_voice_cue(CancellationToken()))
                    self.generation = closing_token
                    self.speech.submit(conversation.farewell(), closing_token)
                    self.speech.finish(closing_token)
                    continue
                if not result.accepted:
                    self._reject_final(result)
                    continue
                result = self.clarification.consume(result)
                command = detector.command(result.text) if detector.detect_text(result.text) else result.text
                conversation.touch()
                if self.audio_focus: self.audio_focus.acquire()
                if not command:
                    self.state.set(AgentState.LISTENING, 'Слушаю…')
                    continue
                self.state.set(AgentState.THINKING, result.text)
                self.dispatch(command, result)
                if self.performance:
                    self.performance.record('voice_final_ready', data['ready']-data['queued'],
                        basis='VAD endpoint to final transcript; includes decoder queue',
                        trace_id=data.get('trace_id'))
        finally:
            barge.reject('shutdown')
            self.capture.close()
            decoder.close()
            self.decoder = None
            if self.audio_focus: self.audio_focus.close()
