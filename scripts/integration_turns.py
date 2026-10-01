"""Run explicit test turns through the running Onyx production queue."""
from pathlib import Path
import argparse
import json
from time import time_ns
from kk.core.control import request_control

ROOT = Path(__file__).resolve().parents[1]


def run(text, spoken=False):
    result = request_control(ROOT, 'ask', timeout=340, text=text, spoken=spoken, diagnostic=True)
    trace = result.get('trace', [])
    origin = next((e['monotonic'] for e in trace if e['event'] in {'text_committed', 'voice_final_transcript'}), None)
    events = {}
    if origin is not None:
        for name in ('brain_first_content', 'audio_first_callback', 'speech_complete'):
            times = [e['monotonic'] for e in trace if e['event'] == name]
            if times: events[name] = min(times)-origin
    completions = [e for e in trace if e['event'] == 'audio_playback_complete']
    folder = ROOT/'data/integration/turns'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/f'{time_ns()}.json'
    path.write_text(json.dumps({'request':text, 'spoken':spoken, 'result':result},ensure_ascii=False,indent=2),encoding='utf-8')
    summary = {k:v for k,v in result.items() if k not in {'trace','actions','metrics'}}
    summary.update(path=str(path), timing_events=events, playback_chunks=len(completions),
                   tools=[a['tool'] for a in result.get('actions', [])])
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('texts', nargs='+')
    parser.add_argument('--spoken', action='store_true')
    args=parser.parse_args()
    for text in args.texts:
        run(text,args.spoken)
