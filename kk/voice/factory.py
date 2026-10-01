from __future__ import annotations

from dataclasses import dataclass

from kk.config import Config
from kk.runtime import Runtime

from .loop import VoiceLoop
from .stt_faster_whisper import FasterWhisperSTT
from .tts_silero import SileroTTS


@dataclass(slots=True)
class VoiceServices:
    stt: FasterWhisperSTT
    tts: SileroTTS
    loop: VoiceLoop


def build_voice_services(config: Config, runtime: Runtime) -> VoiceServices:
    data_models = config.root / "data" / "models"
    stt = FasterWhisperSTT(
        config.get("voice.stt_model", "small"),
        config.get("voice.stt_device", "cpu"),
        config.get("voice.stt_compute_type", "int8"),
        config.get("voice.language", "auto"),
        config.get("voice.microphone"),
        data_models / "faster-whisper",
        runtime.performance,
    )
    from .voices import build_tts
    tts = build_tts(config, runtime.performance)
    def pronunciations():
        # Existing memory adapter owns user facts; no second dictionary store.
        return {row['key'].removeprefix('pronunciation.'): row['value']
                for row in runtime.memory.recall('pronunciation', 30)
                if row['key'].startswith('pronunciation.') and row.get('source') == 'user'}
    tts.pronunciation = pronunciations
    manager=getattr(runtime.provider,'resource_manager',None)
    if manager:
        stt.before_load=manager.prepare_stt
        tts.before_load=manager.prepare_tts
    stt.cpu_threads = int(config.get('voice.stt_cpu_threads',0))
    stt.hotwords = config.get('voice.hotwords', [])
    from .error_dataset import VoiceErrorDataset
    errors = VoiceErrorDataset(config.root/'data'/'voice_diagnostics')
    stt.hotwords += errors.hotwords()
    stt.resolver = getattr(runtime.tools, 'app_resolver', None)
    stt.personal_intents = runtime.tools.personal_intents
    stt.hotwords = list(dict.fromkeys(stt.hotwords + ['Discord', 'DS', 'TG', 'GPT', 'ChatGPT', 'Hearthstone', 'Battle.net', 'ifconfig', 'ipconfig'] + list(stt.personal_intents.entries)))
    # Realtime retries reuse the loaded turbo model. CPU large-v3 remains a
    # separate diagnostic option, never an interactive fallback.
    for profile in ('final_options', 'retry_options'):
        getattr(stt, profile).update(config.get('voice.' + profile, {}))
    for key in ('log_prob_threshold', 'no_speech_threshold', 'compression_ratio_threshold',
                'hallucination_silence_threshold', 'minimum_word_probability'):
        setattr(stt, key, config.get('voice.'+key, getattr(stt, key)))
    loop = VoiceLoop(
        runtime.agent,
        stt,
        tts,
        runtime.state,
        runtime.policy,
        config.get("voice.push_to_talk_hotkey", "ctrl+space"),
        runtime.performance,
    )
    from .diagnostics import LastVoiceSample
    loop.last_voice_sample = LastVoiceSample(config.root/'data'/'voice_diagnostics')
    tts.cpu_threads = int(config.get('voice.tts_cpu_threads',2))
    loop.record_options = {'silence_seconds':config.get('voice.silence_seconds', .6),
                           'max_seconds':config.get('voice.max_record_seconds',30),
                           'minimum_speech_seconds':config.get('voice.minimum_speech_seconds',.06),
                           'silence_threshold':config.get('voice.silence_threshold',.0015),
                           'pre_roll_seconds':config.get('voice.pre_roll_seconds',.3),
                           'post_roll_seconds':config.get('voice.post_roll_seconds',.18)}
    loop.wake_phrase = config.get('wakeword.phrase','оникс')
    loop.barge_in_mode = config.get('voice.barge_in_mode','wake_phrase')
    loop.conversation_timeout = config.get('voice.conversation_timeout', 25)
    loop.streaming_options = dict(
        silence=config.get('voice.silence_seconds', .75),
        incomplete_silence=config.get('voice.incomplete_silence_seconds', 1.8),
        unknown_silence=config.get('voice.unknown_silence_seconds', 1.2),
        minimum_speech=config.get('voice.minimum_speech_seconds', .12),
        maximum=config.get('voice.max_record_seconds', 30),
        pre_roll=config.get('voice.pre_roll_seconds', .3),
        partial_interval=config.get('voice.partial_interval_seconds', 1.0))
    loop.manual_maximum = config.get('voice.manual_max_record_seconds', 300)
    from .audio_focus import AudioFocusManager
    loop.audio_focus = AudioFocusManager(config.root/'data'/'audio_focus.json',
        config.get('voice.ducking_enabled', True), config.get('voice.ducking_level', .25),
        config.get('voice.ducking_fade_ms', 180))
    runtime.tools.audio_focus = loop.audio_focus
    if config.get('voice.aec_enabled', True):
        from .echo import EchoCancellation
        try:
            loop.echo = EchoCancellation(loop.speech.render_reference, config.get('voice.aec_delay_ms', 50))
        except (ImportError, OSError) as exc:
            import logging
            logging.getLogger('kk').warning('[AEC] Unavailable: %s; microphone stays open',exc)
    if config.get('wakeword.mode') == 'native':
        from .wakeword import NativeWakeWordProvider
        try:
            loop.native_wakeword = NativeWakeWordProvider(config.root/config.get('wakeword.model_path','data/models/wakeword/key_key.onnx'),
                config.get('wakeword.threshold',.65),config.get('wakeword.consecutive_frames',2))
        except (ImportError, FileNotFoundError) as exc:
            import logging
            logging.getLogger('kk').warning('[WAKEWORD] Native unavailable: %s; STT fallback',exc)
    return VoiceServices(stt, tts, loop)
