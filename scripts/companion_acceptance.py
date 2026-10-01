"""Explicitly launched, resumable Focusrite acceptance; no tool actions in STT modes."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import monotonic, sleep
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)

def stt_service(config, model=None, device=None, compute=None):
    from kk.voice.stt_faster_whisper import FasterWhisperSTT
    stt=FasterWhisperSTT(model or config.get('voice.stt_model'),device or config.get('voice.stt_device'),
        compute or config.get('voice.stt_compute_type'),'auto',config.get('voice.microphone'),ROOT/'data/models/faster-whisper')
    stt.cpu_threads=config.get('voice.stt_cpu_threads',6)
    stt.hotwords=config.get('voice.hotwords',[])
    return stt

def record(config, folder):
    import soundfile as sf
    from kk.voice.acceptance_corpus import corpus
    path=folder/'manifest.json'
    data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(
        source='physical_microphone',device=config.get('voice.microphone'),created=datetime.now(timezone.utc).isoformat(),trials=[])
    print('110 фраз, с возможностью продолжить позже. Команды НЕ исполняются.\n'
          'WAV сохраняются только для этой acceptance-сессии; обычный voice mode их не сохраняет.\n'
          'Enter — записать; s — пропустить; q — выйти. После сигнала прочитайте фразу.')
    stt=stt_service(config)
    completed={r['id'] for r in data['trials']}
    for index,prompt in enumerate(corpus(),1):
        if prompt['id'] in completed: continue
        choice=input(f"\n[{index}/110] {prompt['expected']}\n> ").strip().lower()
        if choice=='q': break
        if choice=='s': continue
        try:
            audio=stt.record_until_silence(in_memory=True,ready_beep=True,calibration_seconds=1,
                silence_seconds=1.8,max_seconds=30,minimum_speech_seconds=.12)
            if input('Вы произнесли фразу точно? Enter=сохранить, r=повторить позже: ').strip().lower()=='r': continue
            wav=folder/(prompt['id']+'.wav'); sf.write(wav,audio,16000,subtype='PCM_16')
            data['trials'].append(dict(prompt,path=wav.name,seconds=len(audio)/16000,capture=stt.last_capture))
            save(path,data)
        except TimeoutError: print('Речь не обнаружена; фраза не засчитана.')
    print(f"Сохранено {len(data['trials'])}/110: {path}")

def benchmark(config, folder):
    from scripts.benchmark_companion_stt import errors
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    result=dict(source=manifest['source'],live_acceptance_complete=False,models={})
    # Separate subprocesses free CUDA model allocations between candidates.
    import subprocess, os
    for model in ('small','large-v3-turbo'):
        out=folder/(model+'.json')
        subprocess.run([sys.executable,str(Path(__file__).resolve()),'--mode','score-model',
            '--folder',str(folder),'--model',model],cwd=ROOT,env={**os.environ,'PYTHONUTF8':'1'},check=True)
        result['models'][model]=json.loads(out.read_text(encoding='utf-8'))
    result['dataset_complete']=len(manifest['trials'])==110
    result['quality_accepted_by_user']=False
    save(folder/'comparison.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

def score_model(config, folder, model):
    from scripts.benchmark_companion_stt import errors
    data=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    stt=stt_service(config,model)
    stt.prewarm(); rows=[]
    for trial in data['trials']:
        heard=stt.transcribe(folder/trial['path'])
        rows.append(dict(trial,transcription=asdict(heard),errors=errors(trial['expected'],heard.text)))
    summary={}
    for group in ('ru','en','mixed','technical','short'):
        part=[r for r in rows if r['group']==group]
        if not part: continue
        words=sum(r['errors']['reference_words'] for r in part)
        chars=sum(r['errors']['chars'] for r in part)
        summary[group]=dict(count=len(part),wer=sum(r['errors']['word_errors'] for r in part)/max(1,words),
            cer=sum(r['errors']['char_errors'] for r in part)/max(1,chars),
            rejected=sum(not r['transcription']['accepted'] for r in part),
            median_seconds=float(np.median([r['transcription']['latency_seconds'] for r in part])),
            p90_seconds=float(np.percentile([r['transcription']['latency_seconds'] for r in part],90)))
    save(folder/(model+'.json'),dict(model=model,device=stt.device,compute=stt.compute_type,summary=summary,trials=rows))

def interruptions(config, folder):
    from queue import Queue,Empty
    from kk.voice.streaming import AudioCapture,TurnManager,VAD,StreamingSTT,BargeInController
    from kk.voice.speech_queue import SpeechQueue
    from kk.voice.echo import EchoCancellation
    from kk.voice.tts_silero import SileroTTS
    stt=stt_service(config); stt.prewarm()
    tts=SileroTTS(ROOT/'data/models/tts/silero_ru.pt',config.get('voice.tts_model_url'))
    tts.prewarm(); speech=SpeechQueue(tts)
    events=Queue(); timings=[]; rows=[]
    decoder=StreamingSTT(stt,lambda *e:events.put(e))
    def interrupt():
        start=monotonic(); speech.cancel(); timings.append(dict(vad_detected=start,stop_returned=monotonic()))
    capture=AudioCapture(stt.microphone,TurnManager(),VAD(),decoder,
        lambda *e:events.put(e),interrupt,EchoCancellation(speech.render_reference,config.get('voice.aec_delay_ms',50)))
    capture.paused.set(); capture.start()
    try:
        print('20 живых перебиваний. После Enter KK говорит; перебейте его любой фразой. Tools отключены.')
        for i in range(20):
            if input(f'[{i+1}/20] Enter=начать, q=выход: ').lower()=='q': break
            while not events.empty(): events.get_nowait()
            timings.clear(); decoder.invalidate(); capture.paused.clear()
            token=speech.begin()
            speech.submit('Сейчас я расскажу о работе двух мониторов. На первом экране можно открыть редактор, а на втором документацию. Перебей меня своей командой.',token)
            speech.finish(token)
            started=monotonic(); result=None
            while monotonic()-started < 30:
                try: kind,sequence,payload=events.get(timeout=.1)
                except Empty: continue
                if kind=='final': result=payload['result']; break
            capture.paused.set(); speech.cancel()
            print(result.text if result else 'Нет final transcript')
            human=input('Старый голос прекратился, новый transcript верен, старая очередь не вернулась? y=да: ').lower()=='y'
            row=dict(index=i+1,timings=list(timings),transcription=asdict(result) if result else None,human_pass=human)
            rows.append(row)
            latencies=[(t['stop_returned']-t['vad_detected'])*1000 for r in rows for t in r['timings']]
            save(folder/'interruptions.json',dict(source='physical_microphone',trials=rows,
                basis='confirmed VAD event to playback stop return; not independently labelled acoustic onset',
                median_ms=float(np.median(latencies)) if latencies else None,
                p90_ms=float(np.percentile(latencies,90)) if latencies else None))
    finally: capture.close(); decoder.close(); speech.close()

def hallucinations(config, folder):
    from kk.voice.audio_devices import find_device
    from kk.voice.audio_input import AudioInput
    stt=stt_service(config); stt.prewarm(); rows=[]
    scenarios=[('silence',120),('keyboard',30),('mouse',30),('music',30),('video',30),('own_tts',30),('distant_voice',30)]
    print('Проверка отсутствия phantom transcripts. Tools отключены. Подготовьте указанный источник звука.')
    for scenario,seconds in scenarios:
        if input(f'{scenario}, {seconds}s. Enter=записать, q=выход: ').lower()=='q': break
        frames=[]
        speech=echo=None
        if scenario=='own_tts':
            from kk.voice.tts_silero import SileroTTS
            from kk.voice.speech_queue import SpeechQueue
            from kk.voice.echo import EchoCancellation
            tts=SileroTTS(ROOT/'data/models/tts/silero_ru.pt',config.get('voice.tts_model_url'))
            tts.prewarm(); speech=SpeechQueue(tts)
            echo=EchoCancellation(speech.render_reference,config.get('voice.aec_delay_ms',50))
            token=speech.begin()
            speech.submit('Я рассказываю о двух мониторах. Кей Кей не должен принимать свой голос за команды пользователя. '+
                          'Открой Telegram. Поставь громкость тридцать. Это проверка собственного эха. '*3,token)
            speech.finish(token)
        try:
            with AudioInput(find_device(stt.microphone,input_device=True),480) as mic:
                start=monotonic()
                while monotonic()-start < seconds:
                    pcm,overflow=mic.read(480)
                    if pcm and not overflow: frames.append(echo.process(pcm) if echo else pcm)
        finally:
            if speech: speech.close()
        audio=np.frombuffer(b''.join(frames),dtype=np.int16).astype(np.float32)/32768
        chunks=[]
        for offset in range(0,len(audio),16000*10):
            chunks.append(asdict(stt.transcribe(audio[offset:offset+16000*10])))
        print(json.dumps(chunks,ensure_ascii=False,indent=2))
        rows.append(dict(scenario=scenario,seconds=len(audio)/16000,transcripts=chunks,
                         aec_used=echo is not None,
                         accepted_transcripts=sum(bool(r['text']) and r['accepted'] for r in chunks),
                         human_pass=input('Результат соответствует сценарию? y=да: ').lower()=='y'))
        save(folder/'hallucinations.json',dict(source='physical_microphone',tools_enabled=False,trials=rows))

def main():
    from kk.config import Config
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['record','benchmark','score-model','interruptions','hallucinations'],default='record')
    parser.add_argument('--folder',type=Path,default=ROOT/'data/companion_acceptance/focusrite')
    parser.add_argument('--model',default='large-v3-turbo')
    args=parser.parse_args(); config=Config.load(ROOT/'config.toml')
    if args.mode=='score-model': score_model(config,args.folder,args.model)
    else: globals()[args.mode](config,args.folder)

if __name__=='__main__': main()
