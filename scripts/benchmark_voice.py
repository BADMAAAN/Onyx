from __future__ import annotations

import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import sounddevice as sd

from kk.config import Config
from kk.voice.audio_devices import find_device, list_audio_devices
from kk.voice.stt_faster_whisper import FasterWhisperSTT
from kk.voice.tts_silero import SileroTTS


TEXT = "Кей Кей, открой блокнот и скажи, что находится на экране."


def main() -> None:
    config = Config.load(ROOT / "config.toml")
    models = ROOT / "data" / "models"
    tts = SileroTTS(
        models / "tts" / "silero_ru.pt",
        config.get("voice.tts_model_url"),
        config.get("voice.tts_voice"),
        int(config.get("voice.tts_sample_rate")),
        volume=float(config.get("voice.tts_volume")),
    )
    fixture = ROOT / "data" / "stt_fixture.wav"
    tts_cold = tts.save_wav(TEXT, fixture)
    warm_path = ROOT / "data" / "stt_fixture_warm.wav"
    tts_warm = tts.save_wav("Кей Кей отвечает голосом.", warm_path)

    microphone = find_device(config.get("voice.microphone"), input_device=True)
    sample_rate = 16_000
    mic_started = perf_counter()
    sample = sd.rec(sample_rate, samplerate=sample_rate, channels=1, dtype="float32", device=microphone, blocking=True)
    mic_latency = perf_counter() - mic_started
    mic_rms = float(np.sqrt(np.mean(np.square(sample))))
    mic_peak = float(np.max(np.abs(sample)))

    stt_results = {}
    for model_name in ("small", "medium"):
        engine = FasterWhisperSTT(
            model_name=model_name,
            device="cpu",
            compute_type="int8",
            language="ru",
            microphone=microphone,
            download_root=models / "faster-whisper",
        )
        cold = engine.transcribe(fixture)
        warm = engine.transcribe(fixture)
        stt_results[model_name] = {
            "cold_latency_seconds": cold.latency_seconds,
            "warm_latency_seconds": warm.latency_seconds,
            "text": warm.text,
            "language_probability": warm.language_probability,
            "correct_phrase": "открой блокнот" in warm.text.casefold(),
        }

    result = {
        "reference_text": TEXT,
        "audio_devices": list_audio_devices(),
        "microphone_index": microphone,
        "microphone_capture_seconds": mic_latency,
        "microphone_rms": mic_rms,
        "microphone_peak": mic_peak,
        "tts": {
            "voice": tts.voice,
            "model": str(tts.model_path),
            "cold_synthesis_seconds": tts_cold,
            "warm_synthesis_seconds": tts_warm,
        },
        "stt": stt_results,
    }
    (ROOT / "data" / "voice_benchmark.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    warm_path.unlink(missing_ok=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
