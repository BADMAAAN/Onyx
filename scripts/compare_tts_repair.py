"""Small CPU-only V4 / current official V5 A/B; no audio device or config changes."""
import gc
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEXTS = ['Открыла Яндекс Музыку. Контрольный хвост дошёл.',
         'Закрыла VS Code. Папка Onyx осталась на рабочем столе.',
         'Открыла Obsidian. Мне нравится спокойный голос.']
V5_URL = 'https://models.silero.ai/models/tts/ru/v5_4_ru.pt'


def main():
    import psutil
    import numpy as np
    import soundfile as sf
    from kk.config import Config
    from kk.voice.voices import build_tts
    from kk.voice.tts_silero import SileroTTS
    output = ROOT/'data/voice_diagnostics/real_speech'
    output.mkdir(parents=True, exist_ok=True)
    report = {'texts':TEXTS, 'catalog_source':'https://github.com/snakers4/silero-models/blob/master/models.yml',
        'v5_url':V5_URL, 'samples':[], 'quality':'UNVERIFIED: no subjective human naturalness evaluation',
        'recognition':'Not a test of human voice recognition',
        'ruaccent':'Not stacked: default package accentizer only; separate comparison if homograph review demonstrates a gap.'}
    for label in ('current', 'v5_4'):
        try:
            available = psutil.virtual_memory().available
            if available < 9*1024**3: raise RuntimeError('Preserve 8 GiB RAM reserve plus model-load headroom')
            tts = (build_tts(Config.load(ROOT/'config.toml')) if label == 'current' else
                   SileroTTS(ROOT/'data/models/tts/silero_v5_4_ru.pt', V5_URL, voice='baya', sample_rate=48000))
            tts.fallback_voice = None
            # Download with bounded network time; never replace current weights.
            if not tts.model_path.exists():
                temporary = tts.model_path.with_suffix('.download')
                with urlopen(tts.model_url, timeout=60) as source, temporary.open('wb') as target:
                    while block := source.read(1024*1024): target.write(block)
                temporary.replace(tts.model_path)
            started = perf_counter()
            tts.prewarm()
            row = {'variant':label, 'runtime':tts.runtime_diagnostics(), 'load_warm_seconds':perf_counter()-started, 'samples':[]}
            for index, text in enumerate(TEXTS):
                started = perf_counter()
                audio = tts.synthesize(text)
                path = output/f'{label}_{index+1}.wav'
                sf.write(path, audio, tts.sample_rate)
                row['samples'].append({'text':text,'speech_text':tts.prepare_text(text),'path':str(path),
                    'frames':len(audio),'sample_rate':tts.sample_rate,'duration_seconds':len(audio)/tts.sample_rate,
                    'synthesis_seconds':perf_counter()-started,'finite':bool(np.isfinite(audio).all())})
            report['samples'].append(row)
            del tts
            gc.collect()
        except Exception as exc:
            report['samples'].append({'variant':label, 'status':'BLOCKED', 'error':f'{type(exc).__name__}: {exc}'})
        (output/'tts_ab.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(label, report['samples'][-1].get('status','generated'), flush=True)
    return report


if __name__ == '__main__': main()
