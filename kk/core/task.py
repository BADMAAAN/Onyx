from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Event
from queue import Queue
from time import monotonic
import uuid
import logging

_SESSION_ID = uuid.uuid4().hex


class TaskStatus(str, Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    WAITING = 'WAITING'
    VERIFYING = 'VERIFYING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    BLOCKED = 'BLOCKED'
    UNVERIFIED = 'UNVERIFIED'
    CANCELLED = 'CANCELLED'


@dataclass
class CancellationToken:
    generation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    event: Event = field(default_factory=Event)
    completed: Event = field(default_factory=Event)
    deadline: float | None = None
    trace: list = field(default_factory=list)
    updates: Queue = field(default_factory=Queue)
    interruption_pending: Event = field(default_factory=Event)
    session_id: str = field(default_factory=lambda: _SESSION_ID)
    turn_id: str | None = None
    response_id: str | None = None
    diagnostic: bool = False
    cancel_reason: str | None = None
    cancel_source: str | None = None

    def __post_init__(self):
        self.turn_id = self.turn_id or self.generation_id
        self.response_id = self.response_id or self.generation_id

    def mark(self, event, **data):
        if not self.diagnostic:
            for key in ('text', 'transcript', 'final_transcript', 'raw_transcript', 'request',
                        'intent', 'answer', 'context', 'target', 'arguments', 'result', 'speech_text'):
                data.pop(key, None)
        entry = {'event': event, 'monotonic': monotonic(),
                 'session_id': self.session_id, 'turn_id': self.turn_id,
                 'response_id': self.response_id,
                 'task_id': getattr(self, 'trace_id', self.generation_id), **data}
        self.trace.append(entry)
        if hasattr(self, "on_event"):
            self.on_event(entry)
        # Technical logs retain boundaries and reasons, never conversation text.
        if event in {'voice_final_transcript', 'cancelled', 'worker_error', 'audio_playback_complete', 'speech_complete'}:
            logging.getLogger('kk').info('[VOICE_TRACE] %s',
                {k: v for k, v in entry.items() if k not in {'text','transcript','raw_transcript',
                    'final_transcript','request','intent','answer','context','target','arguments','result','speech_text'}})

    def speech_child(self):
        child = CancellationToken(session_id=self.session_id, turn_id=self.turn_id,
                                  response_id=self.response_id, diagnostic=self.diagnostic)
        child.trace_id = getattr(self, 'trace_id', self.generation_id)
        return child

    def cancel(self, reason='superseded', source='runtime'):
        if not self.event.is_set():
            self.cancel_reason, self.cancel_source = reason, source
            self.mark('cancelled', reason=reason, source=source)
        self.interruption_pending.clear()
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise InterruptedError('Generation cancelled: '+str(self.cancel_reason or 'superseded'))
        if self.deadline is not None and monotonic() > self.deadline:
            raise TimeoutError('Generation deadline exceeded')


@dataclass
class AgentTask:
    user_request: str
    max_steps: int = 24
    timeout_seconds: float = 120
    cancellation_token: CancellationToken = field(default_factory=CancellationToken)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_step: int = 0
    observations: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    result: str | None = None
    entities: dict = field(default_factory=dict)
    expected_state: dict = field(default_factory=dict)
    verification: list = field(default_factory=list)
    retries: int = 0
    started: float = field(default_factory=monotonic)
    initial_request: str = field(init=False)

    def __post_init__(self):
        self.initial_request = self.user_request
        self.id = getattr(self.cancellation_token,'trace_id',self.cancellation_token.generation_id)
        from .request_scope import request_scope
        self.request_scope = request_scope(self.user_request)
        self.cancellation_token.request_scope = self.request_scope

    @property
    def original_request(self): return self.initial_request

    @property
    def final_transcript(self):
        evidence = getattr(self.cancellation_token, 'voice_evidence', None)
        return evidence.text if evidence else self.user_request

    @property
    def goal(self): return self.user_request

    def check(self):
        self.cancellation_token.check()
        if monotonic() - self.started > self.timeout_seconds:
            raise TimeoutError('Task deadline exceeded')

    def before_action(self, name, arguments, observation):
        self.check()
        if self.current_step >= self.max_steps:
            raise RuntimeError('Task step limit exceeded')
        import json
        def stable(obj):
            if isinstance(obj, dict):
                return {k:stable(v) for k,v in obj.items() if k not in {'seconds','timestamp','time'}}
            if isinstance(obj, list):
                return [stable(v) for v in obj]
            return obj
        signature = {'tool': name, 'arguments': arguments, 'before': stable(observation)}
        if len(self.actions) >= 2 and all(
            all(a.get(k) == v for k, v in signature.items()) for a in self.actions[-2:]
        ):
            raise RuntimeError('Stuck: third identical action without state change blocked; replan required')
        self.current_step += 1
        self.actions.append(signature)
        self.status = TaskStatus.RUNNING
