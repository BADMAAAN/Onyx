"""Local Russian voice catalog; shares one model and the existing speech queue."""
from .tts_silero import SileroTTS

VOICES = {'xenia': 'Ксения — женский, прежний', 'kseniya': 'Ксения — женский, Onyx default candidate',
          'baya': 'Бая — женский, fallback', 'aidar': 'Айдар — мужской', 'eugene': 'Евгений — мужской'}
PREVIEW_TEXT = 'Привет. Я рядом. Открыла Firefox и нашла видео на YouTube. Discord — на втором экране. Так лучше? Хорошо, включаю музыку. Если что — позови.'


def build_tts(config, performance=None):
    tts = SileroTTS(config.root/'data/models/tts/silero_ru.pt', config.get('voice.tts_model_url'),
        config.get('voice.tts_voice', 'kseniya'), int(config.get('voice.tts_sample_rate', 48000)),
        config.get('voice.output_device'), float(config.get('voice.tts_volume', 1)),
        bool(config.get('voice.tts_muted', False)), performance)
    tts.cpu_threads = int(config.get('voice.tts_cpu_threads', 2))
    tts.rate = config.get('voice.tts_rate','medium')
    tts.pitch = config.get('voice.tts_pitch','medium')
    tts.fallback_voice = 'kseniya' if tts.voice == 'baya' else 'baya'
    if config.get('voice.tts_english_voice'):
        from .tts_sapi import SapiEnglishTTS
        tts.english = SapiEnglishTTS(config.get('voice.tts_english_voice'), tts.sample_rate)
    return tts


def preview(config):
    tts = build_tts(config)
    tts.muted = False
    tts.fallback_voice = None  # A failed variant must not masquerade as another voice.
    model = tts._load()
    available = [v for v in ('xenia','kseniya','baya') if v in getattr(model, 'speakers', VOICES)]
    rows = []
    print(f'Темп: {tts.rate}; тон: {tts.pitch}. Обработка: пунктуация, произношение названий, плавные края PCM.',flush=True)
    try:
        for index, voice in enumerate(available, 1):
            print(f'[{index}/{len(available)}] {voice}: {VOICES[voice]}', flush=True)
            tts.voice = voice
            tts.prewarm()
            latency = tts.speak(PREVIEW_TEXT)
            rows.append({'voice': voice, 'first_audio_seconds': latency})
            print(f'  Первый звук: {latency:.3f} с', flush=True)
    finally:
        tts.stop()
    return rows
