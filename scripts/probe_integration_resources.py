"""Sequential local STT/Brain/TTS measurements; never opens the microphone."""
from pathlib import Path
import json
import logging
from time import perf_counter

from kk.config import Config
from kk.providers.ollama_provider import OllamaProvider
from kk.providers.resources import ModelResourceManager
from kk.voice.stt_faster_whisper import FasterWhisperSTT
from kk.voice.voices import build_tts


def main():
    root = Path(__file__).resolve().parents[1]
    config = Config.load(root/'config.toml')
    logging.basicConfig(level=logging.INFO)
    provider = OllamaProvider(config.get('providers.ollama.base_url'), 180, 4096)
    manager = ModelResourceManager(provider, 8, 2, 2.5)
    provider.resource_manager = manager
    stt = FasterWhisperSTT(config.get('voice.stt_model'), config.get('voice.stt_device'),
        config.get('voice.stt_compute_type'), 'auto', config.get('voice.microphone'),
        root/'data/models/faster-whisper')
    stt.before_load = manager.prepare_stt
    stt.cpu_threads = config.get('voice.stt_cpu_threads', 6)
    rows = {'before': manager.snapshot()}
    start = perf_counter()
    stt.prewarm()
    rows['stt_warm_seconds'] = perf_counter()-start
    rows['stt_resident'] = manager.snapshot()
    print('STT ready', flush=True)
    try:
        for i in range(3):
            start = perf_counter()
            response, timings = provider.chat_stream('qwen3:14b',
                [{'role':'user','content':'В одном предложении: из чего делают чай?'}])
            rows.setdefault('brain', []).append({'iteration':i, 'seconds': perf_counter()-start,
                'timings':timings, 'answer':response.content, 'resources':manager.snapshot(),
                'health':manager.last_health})
            print('Brain', i, timings, flush=True)
        tts = build_tts(config)
        tts.before_load = manager.prepare_tts
        tts.prewarm()
        # Punctuation-only cues must not cancel an otherwise healthy utterance.
        for text in ('Из чего делают чай?', 'Ну, чай делают из листьев чайного растения.',
                     'GPT', '...', 'OK', 'Привет! Всё хорошо.'):
            start = perf_counter()
            try:
                audio = tts.synthesize(text)
                result = dict(frames=len(audio), duration=len(audio)/tts.sample_rate)
            except Exception as exc:
                result = {'error': f'{type(exc).__name__}: {exc}'}
            rows.setdefault('tts', []).append(dict(text=text, seconds=perf_counter()-start, **result))
            print('TTS', text, result, flush=True)
    except Exception as exc:
        rows['error'] = f'{type(exc).__name__}: {exc}'
        rows['health'] = manager.last_health
        print(rows['error'], flush=True)
    finally:
        rows['after'] = manager.snapshot()
        manager.ensure_unloaded('qwen3:14b')
        (root/'data/integration').mkdir(exist_ok=True)
        (root/'data/integration/resources.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
