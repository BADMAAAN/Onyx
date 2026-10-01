"""Synthetic replay + co-residency smoke check; never a live microphone claim."""
import json
import logging
from pathlib import Path
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kk.config import Config
from kk.runtime import build_runtime
from kk.voice.factory import build_voice_services
from kk.voice.error_dataset import VoiceErrorDataset


def main():
    import soundfile as sf
    runtime=build_runtime(Config.load(ROOT/'config.toml'),logging.getLogger('master-voice'))
    manager=runtime.provider.resource_manager
    voice=build_voice_services(Config.load(ROOT/'config.toml'),runtime)
    report={'basis':'synthetic TTS replay, not live user speech; no microphone recording or audio playback'}
    try:
        report['before']=manager.snapshot()
        started=perf_counter();voice.stt.prewarm()
        report['stt_prewarm_seconds']=perf_counter()-started
        report['after_stt']=manager.snapshot()
        started=perf_counter();voice.tts.prewarm()
        report['tts_prewarm_seconds']=perf_counter()-started
        text='Расскажи мне про ChatGPT. Открой PowerShell и напиши ifconfig. Перенеси браузер на второй экран.'
        started=perf_counter();pcm=voice.tts.synthesize(text)
        report['tts_seconds']=perf_counter()-started
        report['audio_seconds']=len(pcm)/voice.tts.sample_rate
        folder=ROOT/'data/master_live';folder.mkdir(parents=True,exist_ok=True)
        fixture=folder/'voice_fixture.wav'
        sf.write(fixture,pcm,voice.tts.sample_rate)
        heard=voice.stt.transcribe(fixture)
        report['synthetic_replay']={'expected':text,'heard':heard.text,'accepted':heard.accepted,'seconds':heard.latency_seconds}
        response,timing=runtime.provider.chat_stream(runtime.model,[{'role':'user','content':'Расскажи мне про ChatGPT несколькими предложениями.'}],temperature=0)
        report['brain_with_voice']={'words':len(response.content.split()),'timing':timing,'health':manager.last_health,'resources':manager.snapshot()}
        report['saved_real_corrections']=len(VoiceErrorDataset(ROOT/'data/voice_diagnostics').rows())
    except Exception as exc:
        report['error']=str(exc)
        report['health']=manager.last_health
    finally:
        manager.idle_stop.set()
        for row in manager.snapshot()['models']: manager.ensure_unloaded(row.get('name') or row.get('model'))
        voice.loop.speech.close();runtime.memory.close()
        (ROOT/'data/onyx_master_voice.json').write_text(json.dumps(report,ensure_ascii=True,indent=2,default=str),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in {'before','after_stt','brain_with_voice'}},ensure_ascii=True),flush=True)


if __name__=='__main__': main()
