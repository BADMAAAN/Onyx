"""Synthetic local speech replay, never microphone/live accuracy acceptance."""
from pathlib import Path
from dataclasses import asdict
from time import perf_counter
import json
import numpy as np
from scipy.signal import resample_poly
from kk.config import Config
from kk.voice.voices import build_tts
from kk.voice.stt_faster_whisper import FasterWhisperSTT, Transcription
from kk.voice.recognition import recognize_final
from kk.tools.windows.app_resolver import ApplicationResolver


def main():
    config=Config.load(Path('config.toml'))
    tts=build_tts(config)
    fast=FasterWhisperSTT('large-v3-turbo','cuda','int8_float16','auto',download_root=Path('data/models/faster-whisper'))
    fast.cpu_threads=6
    fast.hotwords=config.get('voice.hotwords')
    fast.resolver=ApplicationResolver(config.get('apps.aliases'))
    fast.resolver.refresh()
    accurate=FasterWhisperSTT('large-v3','cpu','int8','auto',download_root=Path('data/models/faster-whisper'))
    accurate.cpu_threads=6; accurate.beam_size=8; accurate.hotwords=list(fast.hotwords)
    fast.second_pass=accurate
    fast.prewarm()
    report={'basis':'Synthetic Silero xenia, not live user speech','cases':[]}
    volume_audio=None
    for phrase in ['Сделай громкость на сто процентов.', 'Громкость пятьдесят процентов.', 'Громкость тридцать пять.', 'Кей Кей, открой пауэршелл.']:
        audio=resample_poly(tts.synthesize(phrase),1,3).astype(np.float32)
        if volume_audio is None: volume_audio=audio
        started=perf_counter()
        result=fast.transcribe_final(audio)
        row={'source':phrase,'seconds':perf_counter()-started,**asdict(result)}
        report['cases'].append(row)
        print(phrase,'->',result.text,'pass=',result.chosen_stt_pass,'accepted=',result.accepted,flush=True)
    # Forced first-pass fault exercises the real large-v3 decoder on that exact
    # synthetic audio; it does not claim turbo actually made this mistake.
    original=fast.transcribe
    fast.transcribe=lambda _:Transcription('громкость стол процентов','ru',1,3,0,'громкость стол процентов','громкость стол процентов')
    started=perf_counter()
    result=recognize_final(fast,volume_audio)
    fast.transcribe=original
    report['injected_fault']={'basis':'injected invalid first transcript, real second decoder','seconds':perf_counter()-started,**asdict(result)}
    print('Real second pass:',result.text,'accepted=',result.accepted,flush=True)
    Path('data/voice_reliability.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__': main()
