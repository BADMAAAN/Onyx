"""Passive observations for controlled file replay; never repairs STT text."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
from math import gcd
from pathlib import Path
import re
from threading import Lock
from time import monotonic


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def wav_evidence(path, threshold=.0015, endpoint_silence=.75):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from kk.voice.streaming import VAD
    path = Path(path)
    audio, rate = sf.read(path, dtype='float32', always_2d=True)
    if audio.shape[1] != 1:
        raise ValueError('Replay requires mono WAV')
    audio = audio[:, 0]
    if rate != 16000:
        divisor = gcd(rate, 16000)
        audio = resample_poly(audio, 16000//divisor, rate//divisor)
    pcm = np.clip(np.rint(audio*32768), -32768, 32767).astype('<i2').tobytes()
    vad, segments = VAD(threshold), []
    for offset in range(0, len(pcm), 960):
        frame = pcm[offset:offset+960]
        speech, _ = vad.process(frame.ljust(960, b'\0'))
        end = min(offset+960, len(pcm))/32000
        if segments and segments[-1]['speech'] == speech:
            segments[-1]['end_seconds'] = end
        else:
            segments.append(dict(speech=speech, start_seconds=offset/32000, end_seconds=end))
    for segment in segments:
        segment['duration_seconds'] = segment['end_seconds']-segment['start_seconds']
    internal = [s for index, s in enumerate(segments) if not s['speech'] and
                0 < index < len(segments)-1 and s['duration_seconds'] >= endpoint_silence]
    return dict(path=str(path.resolve()), wav_sha256=sha256(path.read_bytes()),
                pcm_sha256=sha256(pcm), original_sample_rate=rate, sample_rate=16000,
                duration_seconds=len(pcm)/32000, frames=len(pcm)//2,
                vad_threshold=threshold, endpoint_silence_seconds=endpoint_silence,
                vad_segments=segments, internal_endpoint_sized_silences=internal,
                endpoint_prediction='Potential splits only; actual endpoint also depends on partial STT')


class ReplayProbe:
    """Wrap callbacks without changing input, confidence, routing or completion."""
    def __init__(self, loop):
        self.loop, self.lock = loop, Lock()
        self.case_id = None
        self.events, self.dispatches, self.speeches = [], [], []
        decoder, capture, speech = loop.decoder, loop.capture, loop.speech
        original_submit, original_emit = decoder.submit, decoder.emit
        original_capture_emit = capture.emit
        original_dispatch, original_begin = loop.dispatch, speech.begin

        def submit(kind, sequence, pcm):
            if kind == 'final_audio':
                self.record('final_audio_submitted', sequence=sequence, epoch=decoder.epoch,
                            pcm_sha256=sha256(pcm), frames=len(pcm)//2)
            return original_submit(kind, sequence, pcm)

        def emit(kind, sequence, data):
            if kind == 'final':
                self.record('stt_final', sequence=sequence, epoch=data['epoch'],
                    trace_id=data.get('trace_id'), frames=data['samples'],
                    queued=data['queued'], ready=data['ready'],
                    transcription=asdict(data['result']),
                    conversation_active_at_emit=bool(loop.conversation.active))
            elif kind == 'error':
                self.record('decoder_error', sequence=sequence, detail=str(data))
            return original_emit(kind, sequence, data)

        def capture_emit(kind, sequence, data):
            if kind in {'speech_start', 'discard', 'error', 'native_wake'}:
                self.record('capture_'+kind, sequence=sequence, detail=data)
            return original_capture_emit(kind, sequence, data)

        def dispatch(text, evidence=None):
            token = original_dispatch(text, evidence)
            with self.lock:
                self.dispatches.append((self.case_id, token, text,
                                        getattr(evidence, 'trace_id', None)))
            return token

        def begin(token=None):
            result = original_begin(token)
            with self.lock:
                self.speeches.append((self.case_id, result, speech.done))
            return result

        decoder.submit, decoder.emit, capture.emit = submit, emit, capture_emit
        loop.dispatch, speech.begin = dispatch, begin

    def record(self, event, **data):
        with self.lock:
            self.events.append(dict(case_id=self.case_id, event=event,
                                    monotonic=monotonic(), **data))

    def begin_case(self, case_id):
        with self.lock:
            self.case_id = case_id

    def settled(self):
        loop = self.loop
        with self.lock:
            tasks = [t for case, t, _, _ in self.dispatches if case == self.case_id and t]
            speeches = [(t, done) for case, t, done in self.speeches if case == self.case_id]
        return (not loop.capture.turn.active and not loop.decoder.busy.is_set() and
                loop.decoder.finals.empty() and loop.decoder.partial is None and
                not loop.agent.orchestrator.current_task and
                all(t.completed.is_set() for t in tasks) and
                all(done.is_set() or t.event.is_set() for t, done in speeches))

    def snapshot(self):
        with self.lock:
            events = deepcopy([e for e in self.events if e['case_id'] == self.case_id])
            dispatches = [(t, text, trace) for case, t, text, trace in self.dispatches if case == self.case_id]
            speeches = [(t, done) for case, t, done in self.speeches if case == self.case_id]
        results = [dict(request=text, source_trace_id=trace,
                        **deepcopy(getattr(t, 'result_snapshot', {}))) for t, text, trace in dispatches if t]
        speech_results = [dict(trace_id=getattr(t, 'trace_id', None), done=done.is_set(),
                          cancelled=t.event.is_set(), trace=deepcopy(t.trace)) for t, done in speeches]
        for event in events:
            if event['event'] != 'stt_final':
                continue
            trace = event['trace_id']
            event['tasks'] = [r['task_id'] for r in results if r.get('source_trace_id') == trace and r.get('task_id')]
            rejected = any(s['trace_id'] == trace and any(t['event'] == 'stt_rejected' for t in s['trace']) for s in speech_results)
            event['disposition'] = ('dispatched' if event['tasks'] else 'stt_clarification_spoken' if rejected
                                    else 'final_not_dispatched')
        return dict(events=events, command_results=results, speech_results=speech_results)


def assess_replay(expected, observation):
    """A completed tail task is not evidence that the complete request worked."""
    from kk.voice.wakeword import STTWakeWordProvider
    detector = STTWakeWordProvider('Оникс')
    finals = [e for e in observation['events'] if e['event'] == 'stt_final']
    submitted = [e for e in observation['events'] if e['event'] == 'final_audio_submitted']
    words = lambda text: re.findall(r'\w+', text.casefold().replace('ё', 'е'))
    actual = finals[0]['transcription']['text'] if len(finals) == 1 else None
    actual_command = detector.command(actual) if actual and detector.detect_text(actual) else actual
    exact = actual is not None and words(expected) == words(actual_command)
    literal_exact = None
    if 'напиши:' in expected.casefold():
        from kk.core.literal_intents import literal_intent
        want, got = literal_intent(expected), literal_intent(actual_command or '')
        literal_exact = bool(want and got and want['text'] == got['text'])
        exact = exact and literal_exact
    results = observation['command_results']
    single = len(finals) == len(submitted) == len(results) == 1
    status = results[0].get('status') if len(results) == 1 else None
    if not single:
        reason = 'Expected one complete final and its one task; inspect per-event dispositions'
    elif not exact:
        reason = 'Final transcript differs from complete source; no tail or substituted payload acceptance'
    elif status not in {'COMPLETED', 'WAITING'}:
        reason = 'Production task did not complete or ask its scoped clarification'
    else:
        reason = 'Whole source preserved through one final and one production task'
    return dict(acceptance_status='PASS' if single and exact and status in {'COMPLETED', 'WAITING'} else 'UNVERIFIED',
                reason=reason, final_transcript=actual, transcript_matches_source=exact,
                literal_payload_exact_match=literal_exact, task_status=status,
                final_event_count=len(finals), final_submission_count=len(submitted))
