"""Comparable full-buffer profiles. Synthetic replay is NOT live acceptance."""
import argparse
import json
from pathlib import Path
from statistics import median
import re
import numpy as np
from scipy.signal import resample_poly
from kk.config import Config
from kk.voice.stt_faster_whisper import FasterWhisperSTT
from kk.voice.voices import build_tts

PHRASES = [
    'Кей Кей, открой PowerShell.', 'Сделай громкость на сто процентов.',
    'Громкость сто, нет, пятьдесят процентов.', 'Кей Кей, ДС.', 'Кей Кей, ТГ.',
    'Открой браузер и GPT.',
    'Открой PowerShell на втором, браузер на первом и Discord на втором.',
    'Хочу поиграть в Hearthstone.',
    'Открой Chrome, нет, Firefox.',
    'Открой PowerShell, потом браузер, хотя сначала браузер, потом PowerShell.',
]


def words(text):
    return re.findall(r'[\w]+', text.casefold().replace('ё','е'))


def wer(expected, actual):
    a, b = words(expected), words(actual)
    row = list(range(len(b)+1))
    for i, word in enumerate(a, 1):
        new = [i]
        for j, other in enumerate(b, 1):
            new.append(min(new[-1]+1, row[j]+1, row[j-1]+(word != other)))
        row = new
    return row[-1]/max(1,len(a))


def semantic_plan(text):
    from kk.core.desktop_commands import parse_plan
    from kk.tools.windows.app_resolver import canonical
    plan = parse_plan(text)
    if plan is None: return None
    return [{k: canonical(v) if k=='app' else v.casefold() if isinstance(v,str) else v
             for k,v in step.items()} for step in plan]


def benchmark(stt, samples, profiles):
    rows = []
    for expected, audio in samples:
        row = {'expected': expected, 'duration_seconds': len(audio)/16000, 'profiles': {}}
        for name, options in profiles.items():
            source = audio[:int(len(audio)*.65)] if name == 'partial_prefix' else audio
            result = stt.transcribe(source, **options)
            row['profiles'][name] = dict(text=result.text, latency=result.latency_seconds,
                wer=wer(expected, result.text), confidence=result.confidence, accepted=result.accepted,
                plan_matches=semantic_plan(expected) == semantic_plan(result.text) if semantic_plan(expected) else None)
        row['partial_to_final_changed'] = row['profiles']['partial_prefix']['text'] != row['profiles']['final']['text']
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, help='JSON [{text, path}] with human-labelled recordings')
    parser.add_argument('--cpu', action='store_true', help='Explicit diagnostic CPU large-v3 benchmark')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = Config.load(root/'config.toml')
    stt = FasterWhisperSTT(config.get('voice.stt_model'), config.get('voice.stt_device'),
        config.get('voice.stt_compute_type'), 'auto', download_root=root/'data/models/faster-whisper')
    stt.cpu_threads = config.get('voice.stt_cpu_threads',6)
    stt.hotwords = config.get('voice.hotwords', [])
    stt.final_options.update(config.get('voice.final_options', {}))
    stt.retry_options.update(config.get('voice.retry_options', {}))
    if args.manifest:
        from faster_whisper.audio import decode_audio
        samples = [(row['text'], decode_audio(str(args.manifest.parent/row['path']),sampling_rate=16000))
                   for row in json.loads(args.manifest.read_text(encoding='utf-8'))]
        source = 'human-labelled recordings; offline replay, not live execution acceptance'
    else:
        tts = build_tts(config)
        tts.cpu_threads = 2
        pronunciations = {'PowerShell':'пауэршелл', 'Discord':'дискорд', 'Hearthstone':'хартстоун',
                          'Chrome':'хром', 'Firefox':'файрфокс', 'GPT':'джи пи ти', 'ДС':'дэ эс', 'ТГ':'тэ гэ'}
        samples=[]
        for text in PHRASES:
            spoken=text
            for latin, phonetic in pronunciations.items(): spoken=spoken.replace(latin,phonetic)
            samples.append((text, resample_poly(tts.synthesize(spoken),1,3).astype(np.float32)))
        source = 'synthetic Silero xenia with explicit Cyrillic pronunciations for Latin names; lexical WER penalizes equivalent spelling/digits; not human accuracy'
    stt.prewarm()
    profiles = dict(partial_prefix={}, current_full={}, final=stt.final_options, retry=stt.retry_options,
        beam1=dict(stt.final_options, beam_size=1), context=dict(stt.final_options, condition_on_previous_text=True),
        temperature=dict(stt.final_options, temperature=.2), no_hotwords=dict(stt.final_options, hotwords=None),
        thresholds=dict(stt.final_options, no_speech_threshold=.5, compression_ratio_threshold=2.2, log_prob_threshold=-.7))
    rows = benchmark(stt, samples, profiles)
    summary = {name: dict(median_seconds=median(r['profiles'][name]['latency'] for r in rows),
        p90_seconds=float(np.percentile([r['profiles'][name]['latency'] for r in rows],90)),
        mean_wer=float(np.mean([r['profiles'][name]['wer'] for r in rows]))) for name in profiles}
    for name in profiles:
        summary[name]['plan_matches'] = sum(r['profiles'][name]['plan_matches'] is True for r in rows)
    report = dict(source=source, model=stt.model_name, device=stt.device, compute_type=stt.compute_type,
        profiles=profiles, summary=summary, utterances=rows)
    if args.cpu:
        cpu = FasterWhisperSTT('large-v3','cpu','int8','auto',download_root=root/'data/models/faster-whisper')
        cpu.cpu_threads=6
        cpu.hotwords=stt.hotwords
        cpu.prewarm()
        report['cpu_diagnostic'] = [dict(text=(result:=cpu.transcribe(audio)).text, latency=result.latency_seconds) for _,audio in samples]
    path = root/'data/final_decode_benchmark.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
