"""Report capture and model phases independently of the aura renderer."""
from datetime import datetime,timezone

def snapshot(loop):
    capture=getattr(loop,'capture',None)
    ready=bool(capture and capture.ready.is_set()) and not loop._exit.is_set()
    phase=getattr(loop,'startup_phase','starting')
    reason=getattr(loop,'startup_error','') or getattr(capture,'last_error','') or ''
    if not ready and not reason:
        reason='Voice stopped; restart the backend' if loop._exit.is_set() else 'Voice loading: '+phase
    state=('unavailable' if reason and (loop._exit.is_set() or getattr(loop,'startup_error','')) else 'loading') if not ready else (
        'paused' if loop.paused else 'recording' if loop.manual_recording else
        'committing' if loop.manual_pending else 'speaking' if loop.speech.playing.is_set() else 'listening')
    return dict(voice_ready=ready,voice_state=state,voice_reason=reason,
        voice_notice=getattr(loop,'voice_notice',''),
        readiness=dict(capture=ready,stt=bool(getattr(loop,'stt_ready',False)),
        tts=bool(getattr(loop,'tts_ready',False)),brain=bool(getattr(loop,'brain_ready',False)),
        phase=phase,timestamp=datetime.now(timezone.utc).isoformat()))
