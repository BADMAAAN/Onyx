"""Compare CPU threading with unchanged small/int8/beam=5 decoding."""
import gc
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


def main():
    root=Path(__file__).resolve().parents[1]
    audio=decode_audio(str(root/'data/stt_fixture.wav'),sampling_rate=16000)
    results=[]
    for threads in (4,6,8,2):
        model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=threads,
            download_root=str(root/'data/models/faster-whisper'),local_files_only=True)
        rows=[]
        for repetition in range(11):
            started=perf_counter()
            segments,_=model.transcribe(audio,language='ru',vad_filter=True,beam_size=5,
                                        condition_on_previous_text=False)
            text=' '.join(s.text.strip() for s in segments)
            seconds=perf_counter()-started
            if repetition: rows.append({'seconds':seconds,'text':text})
        numbers=[r['seconds'] for r in rows]
        result={'threads':threads,'samples':rows,'median':float(np.median(numbers)),
                'p90':float(np.percentile(numbers,90)),'best':min(numbers),'worst':max(numbers)}
        results.append(result)
        (root/'data/stt_threads_benchmark.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print({k:v for k,v in result.items() if k!='samples'},flush=True)
        del model; gc.collect()

if __name__=='__main__': main()
