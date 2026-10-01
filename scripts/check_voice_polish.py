"""Offline PCM/prosody check; does not open microphone or play audio."""
from pathlib import Path
import json
import sys
from time import perf_counter
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from kk.config import Config
from kk.voice.voices import build_tts, PREVIEW_TEXT

def main():
    tts=build_tts(Config.load(ROOT/'config.toml')); tts.fallback_voice=None
    tts.prewarm(); rows=[]
    for voice in ('xenia','kseniya','baya'):
        tts.voice=voice; started=perf_counter(); audio=tts.synthesize(PREVIEW_TEXT)
        rows.append(dict(voice=voice,seconds=perf_counter()-started,audio_seconds=len(audio)/tts.sample_rate,
            starts_zero=float(audio[0])==0,ends_zero=float(audio[-1])==0,peak=float(abs(audio).max())))
    tts.voice='xenia'; tts.rate='fast'; tts.pitch='low'
    audio=tts.synthesize('Нашла. Открыла Firefox. Так лучше?')
    assert len(audio)>0 and float(audio[0])==0
    result=dict(voices=rows,ssml_rate_pitch='fast/low synthesis passed',playback_tested=False)
    (ROOT/'data'/'polish_tts.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()
