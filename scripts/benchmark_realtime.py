from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import subprocess
from time import perf_counter, sleep
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import psutil


def resources():
    process = psutil.Process()
    data = {'process_ram_mib':process.memory_info().rss/2**20,
            'process_cpu_seconds':sum(process.cpu_times()[:2]),
            'system_ram_used_mib':psutil.virtual_memory().used/2**20}
    try:
        result = subprocess.run(['nvidia-smi','--query-gpu=memory.used,utilization.gpu','--format=csv,noheader,nounits'],
            capture_output=True,text=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
        values = result.stdout.strip().split(',')
        data.update(gpu_total_used_mib=float(values[0]),gpu_utilization_percent=float(values[1]))
    except Exception as exc:
        data['gpu_error'] = str(exc)
    return data


def run_benchmark(config, runtime):
    from kk.voice.factory import build_voice_services
    from kk.voice.speech_queue import SentenceBuffer
    from kk.core.task import CancellationToken
    from kk.vision.base import ScreenObservation
    result = {'date':datetime.now(timezone.utc).isoformat(), 'note':'Synthetic/WAV benchmark; NOT live microphone acceptance',
              'ocr':[], 'vision':[], 'resources_idle':resources()}
    runtime.context.start()
    before = resources()
    sleep(5)
    after = resources()
    result['idle_process_cpu_percent_one_core'] = (after['process_cpu_seconds']-before['process_cpu_seconds'])/5*100
    runtime.context.stop()
    start = perf_counter()
    observation = runtime.screen.fast_context()
    result['uia_seconds'] = perf_counter()-start
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf',32)
    for language, text in [('ru','СИНИЙ МАЯК 7429'),('en','Save Bluetooth settings 7429'),('mixed','Сохранить Save Bluetooth 7429')]:
        image = Image.new('RGB',(1000,200),'white')
        ImageDraw.Draw(image).text((20,50),text,font=font,fill='black')
        samples = []
        for _ in range(3):
            ocr = runtime.screen.ocr.read(image)
            samples.append(ocr['seconds'])
        result['ocr'].append({'fixture':language,'expected':text,'seconds':samples,
            'recognized':[r['text'] for r in ocr['results']],
            'exact':any(text == r['text'] for r in ocr['results'])})
        print('[OCR]',result['ocr'][-1],flush=True)
    # Recorded WAV is intentionally separate from the live acceptance report.
    voice = build_voice_services(config,runtime)
    try:
        fixture = config.root/'data'/'stt_fixture.wav'
        if fixture.exists():
            result['stt_wav'] = []
            for _ in range(2):
                heard = voice.stt.transcribe(fixture)
                result['stt_wav'].append({'seconds':heard.latency_seconds,'text':heard.text})
        response, timing = runtime.provider.stream_benchmark(runtime.model,[{'role':'user','content':'Скажи кратко: готов к работе.'}])
        result['brain'] = timing
        voice.tts._load()
        token = CancellationToken()
        voice.loop.speech.begin(token)
        buffer = SentenceBuffer(lambda c:voice.loop.speech.submit(c,token))
        start = perf_counter()
        response, result['streaming_brain'] = runtime.provider.chat_stream(runtime.model,
            [{'role':'user','content':'Расскажи тремя короткими предложениями о пользе прогулок.'}],
            on_token=buffer.feed,cancellation=token)
        buffer.flush()
        voice.loop.speech.finish(token)
        voice.loop.speech.wait(token)
        result['time_to_first_audio_seconds'] = voice.loop.speech.first_audio_seconds
        result['streaming_speech_total_seconds'] = perf_counter()-start
        result['resources_command'] = resources()
        # Cooperative barge-in with real Silero and real playback, hotkey-equivalent cancellation.
        token = voice.loop.speech.begin()
        voice.loop.speech.submit('Это длинная фраза для проверки остановки голосового ответа. Следующая фраза не должна прозвучать.',token)
        started = perf_counter()
        if voice.loop.speech.audio_started.wait(20):
            cancel_started = perf_counter()
            voice.loop.speech.cancel()
            result['barge_in_programmatic'] = {'stop_seconds':perf_counter()-cancel_started,
                'token_cancelled':token.event.is_set(), 'text_queue_empty':voice.loop.speech.text_queue.empty(),
                'audio_queue_empty':voice.loop.speech.audio_queue.empty(), 'natural_voice_tested':False}
        else:
            result['barge_in_programmatic'] = {'error':'No audio started in 20s'}
        # Stable known image on the real Windows desktop.
        fixture_text = config.root/'data'/'perception_fixture.txt'
        fixture_text.write_text('СИНИЙ МАЯК 7429\nSave Bluetooth settings\nKK работает',encoding='utf-8')
        subprocess.Popen(['notepad.exe',str(fixture_text)])
        sleep(2)
        from kk.tools.windows.windows import focus_window
        focus_window('perception_fixture.txt — Блокнот')
        ui = runtime.screen.fast_context()
        result['text_perception'] = runtime.screen.see_screen(question='Что написано в текущем окне?')
        rect = next((e['rect'] for e in ui['ui_elements'] if e.get('control_type') in {'DocumentControl','EditControl'} and e.get('rect')),None)
        region = [rect[k] for k in ('left','top','right','bottom')] if rect else None
        if region:
            region[2] = min(region[2],region[0]+800)
            region[3] = min(region[3],region[1]+240)
        from kk.tools.windows.windows import focus_window
        from kk.tools.windows.native import window_rect
        focus_window('perception_fixture.txt — Блокнот')
        master, master_meta = runtime.capture.capture_image('all',max_dimension=16384)
        fg = master_meta['foreground']
        if not fg['title'].startswith('perception_fixture.txt'):
            raise RuntimeError('Benchmark foreground changed; refusing invalid crop comparison')
        active_bounds = window_rect(fg['hwnd'])
        captured_cases=[]
        for label,box,size in [('full',master_meta['bounds'],1920),('active',active_bounds,1280),('target',region or active_bounds,800)]:
            left,top,_,_=master_meta['bounds']
            image=master.crop((box[0]-left,box[1]-top,box[2]-left,box[3]-top))
            original=image.size
            image.thumbnail((size,size),Image.Resampling.LANCZOS)
            meta={'foreground':fg,'bounds':box,'scale':(image.width/original[0],image.height/original[1])}
            captured_cases.append((label,image,meta))
        master.close()
        for label,image,meta in captured_cases:
            encoded = io.BytesIO()
            image.save(encoded,'PNG')
            fg=meta['foreground']
            screen = ScreenObservation(Path(),fg['executable'],fg['title'],image.size,None,(0,0),[],encoded.getvalue(),meta['bounds'],meta['scale'])
            from threading import Event,Thread
            stopped=Event()
            resource_samples=[]
            def sample_resources():
                while not stopped.is_set():
                    resource_samples.append(resources())
                    stopped.wait(.5)
            sampler=Thread(target=sample_resources,daemon=True)
            sampler.start()
            started=perf_counter()
            vision_error=None
            try:
                answer=runtime.screen.provider.answer_about_screen(screen,'Прочитай уникальную строку с числом. Ответь кратко.')
            except Exception as exc:
                answer=''
                vision_error=f'{type(exc).__name__}: {exc}'
            finally:
                stopped.set()
                sampler.join(timeout=6)
            trial={'target':label,'seconds':perf_counter()-started,'resolution':image.size,'png_bytes':len(encoded.getvalue()),
                'answer':answer,'contains_7429':'7429' in answer,'resources':resources(),
                'model_stats':getattr(runtime.provider,'last_image_stats',{}),
                'peak_device_vram_mib':max((s.get('gpu_total_used_mib',0) for s in resource_samples),default=0)}
            trial['error']=vision_error
            if not answer.strip() and vision_error is None:
                trial['error']='Empty visible response; generation budget exhausted or unsupported output'
            result['vision'].append(trial)
            print('[VISION]',trial,flush=True)
    finally:
        voice.loop.speech.close()
        path=config.root/'data'/'realtime_benchmark.json'
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        lines=['# Perception benchmark','',f"Date: {result['date']}",result['note'],'',
            '| Path | Latency | Quality |','|---|---:|---|',f"| UIA | {result['uia_seconds']:.3f}s | Current foreground observation |"]
        for row in result['ocr']:
            lines.append(f"| OCR {row['fixture']} | {min(row['seconds']):.3f}–{max(row['seconds']):.3f}s | exact={row['exact']} |")
        for row in result['vision']:
            lines.append(f"| Vision {row['target']} {row['resolution']} | {row['seconds']:.3f}s | {row.get('error') or 'marker='+str(row['contains_7429'])} |")
        lines += ['', 'Cold/switching samples, one per crop; not a statistical quality study. GPU readings are total device usage, not isolated KK allocation.',
            '', 'Raw measurements: `data/realtime_benchmark.json` (local).', '', '```json',json.dumps({k:v for k,v in result.items() if k not in {'text_perception','vision','ocr'}},ensure_ascii=False,indent=2),'```']
        (config.root/'docs'/'PERCEPTION_BENCHMARK.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Benchmark complete. Full measurements: '+str(config.root/'data'/'realtime_benchmark.json'))
    return 0
