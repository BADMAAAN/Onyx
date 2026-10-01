"""Diagnostic requests to the ONE running production backend, never a new build.

Text mode uses its usual Orchestrator/queue. --spoken checks output playback,
not microphone recognition. Audio-input replay has a separate provenance.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kk.core.control import request_control


def run(text, spoken=False):
    before = request_control(ROOT, 'status')
    result = request_control(ROOT, 'ask', timeout=350, text=text, spoken=spoken, diagnostic=True)
    after = request_control(ROOT, 'status')
    trace = result.get('trace', [])
    task_id = result.get('task_id')
    record = dict(created_utc=datetime.now(timezone.utc).isoformat(), input=text,
        source='production_control_text_with_playback' if spoken else 'production_control_text',
        before=before, result=result, after=after,
        trace_identity_consistent=bool(task_id) and all(event.get('task_id') == task_id for event in trace),
        human_speech_verified=False)
    folder = ROOT / 'data/repair_speech_targets/turns'
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / ((task_id or uuid4().hex) + '.json')
    destination.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps(dict(input=text, task_id=task_id, status=result.get('status'),
        answer=result.get('answer'), error=result.get('error'),
        actions=[dict(tool=a['tool'], ok=a.get('result', {}).get('ok'),
            verified=(a.get('result', {}).get('data') or {}).get('verified')
                if isinstance(a.get('result', {}).get('data'), dict) else None)
            for a in result.get('actions', [])],
        identity_consistent=record['trace_identity_consistent'], path=str(destination)),
        ensure_ascii=False), flush=True)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('texts', nargs='+')
    parser.add_argument('--spoken', action='store_true')
    arguments = parser.parse_args()
    for text in arguments.texts:
        run(text, arguments.spoken)
