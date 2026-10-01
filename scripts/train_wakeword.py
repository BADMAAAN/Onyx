"""Reproducible Russian experimental head on official openWakeWord features.

This does not claim English upstream training data validate Russian detection.
Synthetic holdout is speaker-disjoint; microphone calibration is a separate split.
No datasets or third-party wake phrase models are downloaded automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.request import urlretrieve

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
DATA=ROOT/'data'/'wakeword'
MODELS=ROOT/'data'/'models'/'wakeword'
PHRASES=[('кей кей',1),('Кей-кей!',1),('Кей Кей.',1),('кей',0),('окей',0),('кейси',0),
    ('эй кей',0),('окей кей',0),('эй эй',0),('обычная русская речь',0),
    ('Открой настройки компьютера',0),('Сегодня прекрасная погода',0),
    ('Я читаю книгу и слушаю музыку',0),('Сохрани этот файл',0)]
VOICES=['aidar','eugene','kseniya','xenia','baya']
FEATURE_SHA256={
    'melspectrogram.onnx':'ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f',
    'embedding_model.onnx':'70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f'}


def prepare():
    import soundfile as sf
    from kk.config import Config
    from kk.voice.tts_silero import SileroTTS
    config=Config.load(ROOT/'config.toml')
    DATA.mkdir(parents=True,exist_ok=True)
    MODELS.mkdir(parents=True,exist_ok=True)
    sources={}
    for filename in ['melspectrogram.onnx','embedding_model.onnx']:
        path=MODELS/filename
        url='https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/'+filename
        if not path.exists():
            urlretrieve(url,path)
        sources[filename]={'url':url,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if sources[filename]['sha256'] != FEATURE_SHA256[filename]:
            raise ValueError('Official feature model hash mismatch: '+filename)
    tts=SileroTTS(ROOT/'data/models/tts/silero_ru.pt',config.get('voice.tts_model_url'),sample_rate=24000)
    from scipy.signal import resample_poly
    records=[]
    for voice in VOICES:
        tts.voice=voice
        for index,(phrase,label) in enumerate(PHRASES):
            path=DATA/f'{voice}_{index}.wav'
            if not path.exists():
                audio=resample_poly(tts.synthesize(phrase),2,3)
                sf.write(path,audio,16000)
            records.append({'path':path.name,'phrase':phrase,'label':label,'voice':voice,
                'split':'test' if voice=='baya' else 'train','source':'silero-synthetic',
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        print('[PREPARE]',voice,flush=True)
    (DATA/'manifest.json').write_text(json.dumps({'seed':7429,'upstream':'openwakeword==0.6.0',
        'features':sources,'records':records},ensure_ascii=False,indent=2),encoding='utf-8')


def train(epochs=150):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample
    import torch
    from openwakeword.utils import AudioFeatures
    torch.manual_seed(7429)
    torch.set_num_threads(2)
    rng=np.random.default_rng(7429)
    manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    clips=[]; labels=[]
    for row in manifest['records']:
        if row['split']!='train':
            continue
        path=DATA/row['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('Dataset hash mismatch')
        audio,sr=sf.read(path,dtype='float32')
        assert sr==16000
        for _ in range(12):
            # Resampling jointly perturbs pitch and speaking speed.
            shifted=resample(audio,int(len(audio)/rng.uniform(.82,1.18))).astype(np.float32)
            shifted*=rng.uniform(.3,1.2)
            delay=int(rng.uniform(.03,.15)*16000)
            reverbed=shifted.copy()
            if len(shifted)>delay:
                reverbed[delay:]+=shifted[:-delay]*rng.uniform(.0,.25)
            padded=rng.normal(0,rng.uniform(.001,.015),40000).astype(np.float32)
            endpoint=int(rng.uniform(2.1,2.45)*16000)
            fragment=reverbed[-min(len(reverbed),endpoint):]
            padded[endpoint-len(fragment):endpoint]+=fragment
            clips.append((np.clip(padded,-1,1)*32767).astype(np.int16))
            labels.append(row['label'])
    # Include noise and silence; real negative recordings are still needed for release.
    for _ in range(60):
        clips.append((rng.normal(0,rng.uniform(0,.02),40000)*32767).astype(np.int16)); labels.append(0)
    features=AudioFeatures(melspec_model_path=str(MODELS/'melspectrogram.onnx'),
        embedding_model_path=str(MODELS/'embedding_model.onnx'),inference_framework='onnx',ncpu=2)
    x=features.embed_clips(np.stack(clips),batch_size=32,ncpu=2)[:,-16:,:].astype(np.float32)
    x=torch.from_numpy(x); y=torch.tensor(labels,dtype=torch.float32).reshape(-1,1)
    model=torch.nn.Sequential(torch.nn.Flatten(),torch.nn.LayerNorm(16*96),
        torch.nn.Linear(16*96,64),torch.nn.LayerNorm(64),torch.nn.ReLU(),torch.nn.Linear(64,1))
    optimizer=torch.optim.Adam(model.parameters(),lr=.001)
    for epoch in range(epochs):
        for indices in torch.randperm(len(y)).split(64):
            optimizer.zero_grad()
            pred=model(x[indices])
            loss=torch.nn.functional.binary_cross_entropy_with_logits(pred,y[indices],pos_weight=torch.tensor(3.0))
            loss.backward(); optimizer.step()
        if epoch%30==0:
            print('[TRAIN]',epoch,float(loss.detach()),flush=True)
    model.eval()
    torch.onnx.export(torch.nn.Sequential(model,torch.nn.Sigmoid()),torch.zeros(1,16,96),str(MODELS/'key_key.onnx'),
        input_names=['input'],output_names=['score'],opset_version=17,dynamo=False)
    (MODELS/'training.json').write_text(json.dumps({'seed':7429,'epochs':epochs,'examples':len(y),
        'manifest_sha256':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(),
        'status':'EXPERIMENTAL_SYNTHETIC_ONLY','model_sha256':hashlib.sha256((MODELS/'key_key.onnx').read_bytes()).hexdigest()},indent=2),encoding='utf-8')


def evaluate(threshold=.65):
    import numpy as np
    import soundfile as sf
    from kk.voice.wakeword import NativeWakeWordProvider
    manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    detector=NativeWakeWordProvider(MODELS/'key_key.onnx',threshold)
    rows=[]
    for row in manifest['records']:
        if row['split']!='test':
            continue
        audio,sr=sf.read(DATA/row['path'],dtype='int16')
        assert sr==16000
        pcm=np.concatenate([np.zeros(8000,dtype=np.int16),audio,np.zeros(24000,dtype=np.int16)])
        detector.reset(); activation=None; scores=[]; compute=[]
        for offset in range(0,len(pcm)-1280,1280):
            started=perf_counter()
            output=detector.process(pcm[offset:offset+1280])
            compute.append(perf_counter()-started); scores.append(output['score'])
            if output['activated'] and activation is None:
                activation=(offset+1280)/16000-.5
        rows.append({'phrase':row['phrase'],'label':row['label'],'activated':activation is not None,
            'activation_seconds_from_audio_start':activation,'max_score':max(scores),
            'mean_frame_compute_seconds':float(np.mean(compute))})
    tp=sum(r['label']==1 and r['activated'] for r in rows)
    fp=sum(r['label']==0 and r['activated'] for r in rows)
    positives=sum(r['label']==1 for r in rows)
    report={'threshold':threshold,'source':'held-out Silero baya voice; NOT real microphone',
        'true_positives':tp,'misses':positives-tp,'false_positives':fp,
        'recall':tp/positives,'false_positive_clip_rate':fp/(len(rows)-positives),'trials':rows}
    (DATA/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs'/'WAKEWORD_BENCHMARK.md').write_text('# Wake-word benchmark\n\nNative модель экспериментальная. Live recall/false positives не измерены.\n\n```json\n'+
        json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n\nDefault остаётся STT fallback. Для релиза нужны реальные положительные примеры и длительная обычная речь/музыка.\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


def _evaluate_real(folder):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from kk.voice.wakeword import NativeWakeWordProvider
    manifest_path=folder/'manifest.json'
    rows=json.loads(manifest_path.read_text(encoding='utf-8'))
    model_path=MODELS/'key_key.onnx'
    if not model_path.exists():
        return {'status':'model_missing','source':'physical-microphone','trials':rows}
    detector=NativeWakeWordProvider(model_path,.01,2)
    scored=[]
    for row in rows:
        audio,sr=sf.read(folder/row['path'],dtype='int16')
        if audio.ndim>1: audio=audio[:,0]
        if sr!=16000: audio=resample_poly(audio,16000,sr).astype(np.int16)
        audio=np.concatenate([np.zeros(8000,dtype=np.int16),audio,np.zeros(16000,dtype=np.int16)])
        detector.reset(); scores=[]
        for offset in range(0,max(0,len(audio)-1279),1280):
            scores.append(detector.process(audio[offset:offset+1280])['score'])
        scored.append({**row,'scores':scores,'max_score':max(scores,default=0.0)})
    candidates=[]
    for threshold in np.arange(.20,.951,.025):
        def activated(scores):
            hits=0
            for score in scores:
                hits=hits+1 if score>=threshold else 0
                if hits>=2:return True
            return False
        tp=sum(r['label']==1 and activated(r['scores']) for r in scored)
        fp=sum(r['label']==0 and activated(r['scores']) for r in scored)
        positives=sum(r['label']==1 for r in scored); negatives=len(scored)-positives
        candidates.append({'threshold':round(float(threshold),3),'true_positives':tp,
            'false_positives':fp,'recall':tp/max(1,positives),'false_positive_rate':fp/max(1,negatives)})
    acceptable=[c for c in candidates if c['false_positive_rate']<=.05]
    pool=acceptable or candidates
    selected=max(pool,key=lambda c:(c['recall'],-c['false_positive_rate'],-c['threshold']))
    report={'status':'real_microphone_calibration','source':'physical-microphone',
        'samples':len(scored),'positives':sum(r['label']==1 for r in scored),
        'negatives':sum(r['label']==0 for r in scored),'selected':selected,
        'threshold_candidates':candidates,
        'trials':[{k:v for k,v in r.items() if k!='scores'} for r in scored]}
    (folder/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs'/'WAKEWORD_BENCHMARK.md').write_text(
        '# Wake-word benchmark\n\nNative модель остаётся experimental; default — STT fallback.\n\n'
        f"Physical calibration folder: `{folder.relative_to(ROOT)}`\n\n```json\n"+
        json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n',encoding='utf-8')
    return report


def calibrate():
    from kk.config import Config
    from kk.voice.stt_faster_whisper import FasterWhisperSTT
    import shutil
    from datetime import datetime
    config=Config.load(ROOT/'config.toml')
    stt=FasterWhisperSTT(microphone=config.get('voice.microphone'),download_root=ROOT/'data/models/faster-whisper')
    root=DATA/'calibration'; root.mkdir(parents=True,exist_ok=True)
    # Keep at most three bounded sessions. Users can remove all with the
    # explicit --wakeword-delete-samples command.
    old=sorted((p for p in root.iterdir() if p.is_dir()),key=lambda p:p.name)
    for stale in old[:-2]: shutil.rmtree(stale)
    folder=root/datetime.now().strftime('%Y%m%d-%H%M%S'); folder.mkdir()
    positives=[('кей кей',1)]*24
    negative_phrases=['кей','окей','эй','эй кей','кейси','окей кей','эй эй',
        'Сегодня хорошая погода','Я читаю обычное русское предложение',
        'Открой настройки компьютера','Сохрани этот файл','Музыка играет на фоне',
        'Кей Кей прозвучало в постороннем разговоре']
    negatives=[(phrase,0) for phrase in negative_phrases for _ in range(2)][:24]
    prompts=positives+negatives
    records=[]
    print(f'Wake-word calibration: 24 positive + 24 hard-negative. Folder: {folder}')
    for index,(phrase,label) in enumerate(prompts,1):
        input(f'[{index}/{len(prompts)}] Enter, затем скажите: {phrase}\n')
        def event(kind,data):
            if kind in {'speech_start','speech_end'}: print(f'[{kind}] {data}',flush=True)
        path=stt.record_until_silence(max_seconds=7,wait_for_speech_seconds=20,on_event=event,
                                      in_memory=False)  # explicit dataset recording
        destination=folder/f'{index:03d}_{"positive" if label else "negative"}.wav'
        shutil.move(str(path),destination)
        records.append({'path':destination.name,'phrase':phrase,'label':label,'source':'physical-microphone',
            'split':'calibration_only'})
    (folder/'manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    report=_evaluate_real(folder)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print('Удаление всех calibration samples: .\\run.ps1 --wakeword-delete-samples')
    return 0


def delete_calibration():
    import shutil
    folder=DATA/'calibration'
    if folder.exists(): shutil.rmtree(folder)
    print(f'Удалены calibration samples: {folder}')
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for action in ['prepare','train','evaluate','calibrate']:
        parser.add_argument('--'+action,action='store_true')
    parser.add_argument('--threshold',type=float,default=.65)
    args=parser.parse_args()
    if args.prepare: prepare()
    if args.train: train()
    if args.evaluate: evaluate(args.threshold)
    if args.calibrate: calibrate()
