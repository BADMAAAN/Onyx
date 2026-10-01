"""Transcribe-only sessions on the existing capture/decoder owner.

Results are bounded, ephemeral and polled by identity. No agent, history or
command interpretation is reachable from this controller.
"""
from collections import OrderedDict
from time import monotonic
import re


class Dictation:
    def __init__(self, loop):
        self.loop = loop
        self.sessions = OrderedDict()
        self.active = None

    def handle(self, operation, request):
        with self.loop.input_lock:
            sid = request.get('session_id')
            if not isinstance(sid, str) or not re.fullmatch(r'[a-f0-9]{32}', sid):
                raise ValueError('Invalid dictation session')
            if operation == 'dictation.start':
                if request.get('purpose') != 'dictation': raise ValueError('Expected dictation purpose')
                if sid in self.sessions: return dict(self.sessions[sid])
                loop = self.loop
                if self.active or loop.manual_recording or loop.manual_pending:
                    raise RuntimeError('Finish or cancel the current recording')
                if loop.paused or not loop.capture or not loop.capture.ready.is_set():
                    raise RuntimeError('Microphone unavailable or paused')
                if loop.capture.controls.qsize():
                    raise RuntimeError('Microphone is changing state; retry shortly')
                if getattr(loop, 'conversation', None) and loop.conversation.active:
                    raise RuntimeError('Stop Onyx Live before dictation')
                self.active = sid
                self.sessions[sid] = dict(session_id=sid, purpose='dictation',
                    conversation_id=request['conversation_id'], draft_id=request['conversation_id'],
                    revision=request.get('revision', 0), position=request.get('position', 0),
                    state='recording', text='', started=monotonic(),capture_sequence=None)
                while len(self.sessions)>32: self.sessions.popitem(last=False)
                loop.mic_start()
                if not loop.manual_recording:
                    self.complete(error='Recording did not start')
            elif sid not in self.sessions:
                return dict(session_id=sid, state='error', error='Recording session expired; draft preserved')
            elif operation == 'dictation.commit' and self.active == sid:
                if self.sessions[sid]['state'] == 'recording':
                    self.sessions[sid]['state'] = 'transcribing'
                    self.sessions[sid]['commit_started'] = monotonic()
                    self.loop.mic_commit()
            elif operation == 'dictation.cancel' and self.active == sid:
                self.sessions[sid]['state'] = 'cancelled'
                self.loop.mic_cancel()
                self.active = None
            elif operation not in ('dictation.status','dictation.commit','dictation.cancel'):
                raise ValueError('Unknown dictation operation')
            if self.active==sid and operation=='dictation.status':
                row=self.sessions[sid]
                if not self.loop.manual_recording and not self.loop.manual_pending:
                    self.complete(error='Recording ended without a transcript; draft preserved')
                elif row['state']=='transcribing' and monotonic()-row.get('commit_started',monotonic())>30:
                    self.complete(error='Speech recognition timed out; draft preserved')
            return dict(self.sessions[sid])

    def consume(self, kind, result=None):
        """Called before wake detection, cancel phrases, diagnostics or dispatch."""
        with self.loop.input_lock:
            if not self.active: return False
            if kind in ('error', 'discard'):
                self.complete(error='Speech recognition failed; previous draft preserved')
            elif kind == 'final':
                if result is not None and result.accepted and result.text:
                    self.complete(text=result.text)
                else:
                    self.complete(error='Speech was not recognized; previous draft preserved')
            return True

    def complete(self, text='', error=''):
        with self.loop.input_lock:
            if not self.active: return
            row = self.sessions[self.active]
            row.update(state='error' if error else 'ready', text=text, error=error)
            self.loop.mic_cancel()  # invalidate late decoder callbacks and release ducking
            self.active = None

    def capture_event(self,kind,sequence):
        with self.loop.input_lock:
            if not self.active:return False
            if kind=='manual_started':
                self.sessions[self.active]['capture_sequence']=sequence
                return True
            if kind=='manual_cancelled' and self.sessions[self.active].get('capture_sequence')==sequence:
                self.complete(error='Recording cancelled or empty; draft preserved')
                return True
            return False


def is_stop_live(text):
    # Full matching deliberately rejects quotes, negation and embedded examples.
    return bool(re.fullmatch(
        r'(?i)(?:(?:оникс|onyx)[, ]+)?(?:выключи (?:live assistant|live|лайв(?: ассистента)?|лайф-ассистента)|'
        r'закончи голосовой режим|turn off live assistant)[.!? ]*', text.strip()))


def stop_live(loop):
    with loop.input_lock:
        cid = getattr(loop, 'desktop_live_scope', None)
        token = getattr(loop, 'generation', None)
        if cid and token and getattr(token, 'source', None) == 'voice' and token.session_id == cid:
            token.cancel(reason='live_stopped', source='live_control')
            if getattr(token,'speech_token',None): token.speech_token.cancel(reason='live_stopped',source='live_control')
        speech = getattr(loop, 'speech', None)
        playing = getattr(speech, 'token', None)
        if (speech and playing and playing.session_id == (cid or 'legacy-voice')
                and (getattr(playing, 'source', None) == 'voice'
                     or (token and getattr(token, 'source', None) == 'voice'
                         and playing is getattr(token, 'speech_token', None)))):
            speech.cancel(reason='live_stopped', source='live_control')
        service = getattr(loop,'desktop_service',None)
        if cid and service:
            for rid, pending in list(service.tokens.items()):
                if pending.session_id == cid and getattr(pending,'source',None) == 'voice':service.cancel(rid)
        loop.desktop_live_scope = loop.desktop_turn_scope = loop.desktop_voice_scope = None
        if hasattr(loop, '_trigger'): loop._trigger.clear()
        loop.clarification.pending = None
        if hasattr(loop, 'voice_recovery'): loop.voice_recovery.reset()
        loop.voice_notice = ''
        loop.state.set_desktop_inline_live(False)
        if hasattr(loop, 'barge'): loop.barge.reject('live_stopped')
        if hasattr(loop,'conversation'): loop.conversation.active = False
        if loop.decoder: loop.decoder.invalidate()
        if loop.capture: loop.capture.controls.put('cancel')
        if loop.audio_focus: loop.audio_focus.release()
        from kk.core.state import AgentState
        loop.state.set(AgentState.IDLE if loop.wakeword else AgentState.SLEEPING)
        return dict(live_active=False, wake_listening=bool(loop.wakeword and not loop.paused))
