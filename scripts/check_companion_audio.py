"""Bounded real audio API checks plus explicitly synthetic AEC measurements."""
from pathlib import Path
import json
import sys
from time import sleep
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    from kk.voice.echo import EchoCancellation
    from faster_whisper.audio import decode_audio
    from kk.voice.audio_focus import CoreAudioSessions, AudioFocusTransaction
    from kk.tools.windows.media import MediaController
    from kk.tools.windows.windows import list_visible_windows
    import comtypes
    output={}
    source=decode_audio(str(ROOT/'data/latency_D.wav'),sampling_rate=16000)
    reference=np.tile(source,8)
    delay=800
    echo=np.concatenate([np.zeros(delay,dtype=np.float32),reference[:-delay]])*.6
    cursor=[0]
    def render(count): return reference[cursor[0]:cursor[0]+count]
    aec=EchoCancellation(render,50); processed=[]
    for start in range(0,len(reference)-480,480):
        cursor[0]=start
        pcm=np.clip(echo[start:start+480]*32768,-32768,32767).astype(np.int16).tobytes()
        processed.append(np.frombuffer(aec.process(pcm),dtype=np.int16).astype(np.float32)/32768)
    cleaned=np.concatenate(processed)
    begin=16000*4
    output['synthetic_aec']=dict(source='digital delayed echo, NOT room/Focusrite acceptance',
        erle_db=float(10*np.log10((np.mean(echo[begin:len(cleaned)]**2)+1e-12)/(np.mean(cleaned[begin:]**2)+1e-12))))
    comtypes.CoInitialize()
    try:
        backend=CoreAudioSessions()
        clean=lambda: {k:{p:v for p,v in r.items() if p!='control'} for k,r in backend.sessions().items()}
        before=clean(); windows_before=len(list_visible_windows())
        transaction=AudioFocusTransaction(backend,ROOT/'data/companion_audio_test.json',.25,180)
        try:
            transaction.duck(); sleep(.4)
            during=clean(); owned=list(transaction.owned)
        finally: transaction.restore()
        after=clean()
        output['ducking']=dict(before=before,during=during,after=after,selected=owned,
            restored=all(k in after and abs(after[k]['volume']-before[k]['volume'])<.0001 for k in owned),
            windows_before=windows_before,windows_after=len(list_visible_windows()))
        media=MediaController(); sessions=media.call('list'); output['media_sessions']=sessions
        playing=[s for s in sessions if s.get('playing')]
        if len(playing)==1:
            selected=playing[0]['session_id']
            try:
                output['pause']=media.call('pause',selected); sleep(.4)
                output['paused_info']=media.call('info',selected)
            finally:
                output['resume']=media.call('resume',selected)
            sleep(.4); output['resumed_info']=media.call('info',selected)
    finally: comtypes.CoUninitialize()
    (ROOT/'data/companion_audio_acceptance.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k not in ('media_sessions','ducking')},ensure_ascii=False))
    print('ducked',len(output['ducking']['selected']),'exact restore',output['ducking']['restored'])

if __name__=='__main__': main()
