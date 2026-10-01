"""Deterministic non-microphone fixtures, plus an explicitly supplied own-TTS WAV."""
import argparse
import json
from pathlib import Path
import numpy as np
import soundfile as sf


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',default='data/onyx_benchmark')
    parser.add_argument('--tts-wav',required=True)
    args=parser.parse_args()
    root=Path(args.output_dir).resolve();root.mkdir(parents=True,exist_ok=True)
    rate=16000
    time=np.arange(rate*6)/rate
    melody=np.zeros_like(time)
    for index,freq in enumerate([261.63,329.63,392,523.25,392,329.63,293.66,261.63]):
        local=time-index*.65
        envelope=np.where((local>=0)&(local<.6),np.exp(-np.maximum(local,0)*5),0)
        melody+=envelope*(np.sin(2*np.pi*freq*local)+.35*np.sin(4*np.pi*freq*local))*.14
    sf.write(root/'negative-music.wav',melody,rate)
    notify=np.zeros_like(time)
    for start,freq in [(1,880),(1.25,1174.66),(1.5,1567.98)]:
        local=time-start
        notify+=np.where((local>=0)&(local<.4),np.exp(-np.maximum(local,0)*12)*np.sin(2*np.pi*freq*local)*.2,0)
    sf.write(root/'negative-notification.wav',notify,rate)
    rows=[dict(id='instrumental-synthetic',group='music',audio='negative-music.wav',source='synthetic',expected=''),
          dict(id='notification-synthetic',group='system_audio',audio='negative-notification.wav',source='synthetic',expected=''),
          dict(id='own-tts-reference',group='self_tts',audio=str(Path(args.tts_wav).resolve()),source='synthetic',expected='')]
    (root/'negative_manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
