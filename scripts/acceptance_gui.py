"""Explicit real desktop tests, separate from microphone acceptance."""
from pathlib import Path
import sys
import json
import subprocess
from time import perf_counter,sleep
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from kk.config import Config
from kk.runtime import build_runtime
from kk.logging_setup import configure_logging
from kk.core.instance import SingleInstance
from kk.core.task import CancellationToken
from kk.voice.factory import build_voice_services
from kk.voice.speech_queue import SentenceBuffer


def main():
    config=Config.load(ROOT/'config.toml')
    lock=SingleInstance(ROOT/'data/kk.lock')
    runtime=build_runtime(config,configure_logging(ROOT/'logs',False))
    results={}
    voice=None
    try:
        for name,request,expected in [('A','Открой блокнот и напиши "KK работает"','В Блокноте написано'),
            ('G_candidate','Кей Кей, запомни, как я сейчас это сделал','Предлагаю навык'),
            ('G_confirm','да','Навык сохранён'),
            ('C','Что написано в текущем окне?','KK работает'),
            ('B','Открой настройки Windows и найди Bluetooth','Bluetooth')]:
            started=perf_counter()
            answer=runtime.agent.ask(request)
            task=runtime.agent.orchestrator.last_task
            results[name]={'answer':answer,'passed':expected in answer,'seconds':perf_counter()-started,
                'tools':[a['tool'] for a in task.actions] if task else []}
            print(name,results[name],flush=True)
        viewer=subprocess.Popen([sys.executable,str(ROOT/'scripts/vision_fixture_window.py')])
        try:
            sleep(2)
            focused=runtime.tools.execute('focus_window',{'title':'KK vision acceptance fixture'})
            if not focused.ok:
                raise RuntimeError(focused.error)
            started=perf_counter()
            result=runtime.tools.execute('see_screen',{'question':'Что изображено на экране? Ответь двумя короткими предложениями.','mode':'vision'})
            text=(result.data or {}).get('description','')
            results['D']={'passed':result.ok and 'красн' in text.casefold() and 'син' in text.casefold(),
                'seconds':perf_counter()-started,'description':text,'error':result.error,
                'note':'Real image viewer window, synthetic two-shape image; not a photo accuracy test'}
        finally:
            viewer.terminate(); viewer.wait(timeout=5)
        voice=build_voice_services(config,runtime)
        voice.stt._load(); voice.tts._load()
        # Warm the audio synthesizer before measuring a warm simple voice pipeline.
        voice.tts.synthesize('Готов.')
        fixture=ROOT/'data/simple_voice_fixture.wav'
        voice.tts.save_wav('Кей Кей, открой блокнот.',fixture)
        started=perf_counter()
        heard=voice.stt.transcribe(fixture)
        token=CancellationToken(); voice.loop.speech.begin(token)
        buffer=SentenceBuffer(lambda text:voice.loop.speech.submit(text,token))
        answer=runtime.agent.ask(heard.text,on_token=buffer.feed,cancellation=token)
        buffer.flush(); voice.loop.speech.finish(token); voice.loop.speech.wait(token)
        results['simple_voice_WAV']={'transcription':heard.text,'answer':answer,'stt_seconds':heard.latency_seconds,
            'post_stt_first_audio_seconds':voice.loop.speech.first_audio_seconds,
            'pipeline_first_audio_seconds':heard.latency_seconds+(voice.loop.speech.first_audio_seconds or 0),
            'pipeline_total_seconds':perf_counter()-started,'source':'synthetic WAV, NOT microphone',
            'tools':[a['tool'] for a in runtime.agent.orchestrator.last_task.actions]}
    finally:
        if voice: voice.loop.speech.close()
        runtime.context.stop(); runtime.memory.close(); lock.close()
        (ROOT/'data/gui_acceptance.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        (ROOT/'docs/GUI_ACCEPTANCE.md').write_text('# Real GUI acceptance\n\nTyped commands and synthetic WAV. No live microphone claims.\n\n```json\n'+json.dumps(results,ensure_ascii=False,indent=2)+'\n```\n',encoding='utf-8')
        print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
