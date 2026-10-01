"""Coverage belongs to an observation, never to the number of returned items."""
from datetime import datetime, timezone


def observation(items, *, source, scope, completeness='unknown', limitations=(), timestamp=None):
    if completeness not in {'complete', 'partial', 'unknown'}:
        raise ValueError('Invalid observation completeness')
    if not source or not scope:
        raise ValueError('Observation source and scope are required')
    reasons = list(limitations)
    if completeness != 'complete' and not reasons:
        reasons = ['Coverage has not been established.']
    return dict(source=source, scope=scope,
                timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
                completeness=completeness, items=list(items), limitations=reasons)
