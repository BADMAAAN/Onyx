"""Synthesize local female voices to WAV; does not play audio or open a microphone."""
import json
from pathlib import Path
import sys
from time import perf_counter
import statistics
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kk.config import Config
from kk.voice.voices import build_tts


def main():
    output=ROOT/'data/onyx_benchmark'; output.mkdir(exist_ok=True)
    tts=build_tts(Config.load(ROOT/'config.toml')); tts.fallback_voice=None
    texts=['Привет. Я Оникс. Я рядом.', 'Открыла Firefox и YouTube. Telegram на втором экране.',
           'Громкость пятьдесят. Включила музыку.']
    started=perf_counter(); tts.prewarm(); startup_seconds=perf_counter()-started
    rows=[]
    for voice in ['xenia','kseniya','baya']:
        tts.voice=voice
        tts.synthesize(texts[0])
        for i,text in enumerate(texts):
            started=perf_counter(); pcm=tts.synthesize(text); elapsed=perf_counter()-started
            sf.write(output/f'{voice}_{i}.wav',pcm,tts.sample_rate)
            rows.append(dict(voice=voice,text=text,seconds=elapsed,audio_seconds=len(pcm)/tts.sample_rate,
                rtf=elapsed/(len(pcm)/tts.sample_rate),cold=False,actual_voice=tts.actual_voice))
    report=dict(rows=rows,startup_seconds=startup_seconds,subjective_quality='Not measured; WAV previews are available',
        kokoro_ru={'status':'not_integrated','voices':['sveta','masha'],
            'reason':'Requires separate RUAccent/G2P stack; code-switching and interruption need verification',
            'source':'https://huggingface.co/zaakirio/kokoro-ru'},
        candidate=min(['kseniya','baya'],key=lambda v:statistics.median(r['seconds'] for r in rows if r['voice']==v and not r['cold'])),
        selection_basis='Alternative female candidate with lowest warm synthesis latency; not a naturalness ranking')
    (output/'tts.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
