from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kk.providers.ollama_provider import OllamaProvider
from kk.vision.screen_capture import ScreenCapture


provider = OllamaProvider("http://127.0.0.1:11434", 240, 8192)
brain = "qwen2.5:14b"
vision = "qwen3-vl:8b"


def gpu() -> str:
    return subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu", "--format=csv,noheader"],
        text=True,
    ).strip()


def run_chat(model: str, prompt: str, tools=None, image: str | None = None):
    message = {"role": "user", "content": prompt}
    if image:
        message["images"] = [image]
    messages = [
        {"role": "system", "content": "Ты локальный Windows-агент KK. Используй предоставленные tools, когда пользователь просит проверить компьютер. Отвечай по-русски."},
        message,
    ]
    response, metrics = provider.stream_benchmark(model, messages, tools)
    return {**metrics, "answer": response.content.strip(), "tool_calls": [call.name for call in response.tool_calls]}


def main() -> None:
    shot = ScreenCapture().capture("active_window")
    encoded = base64.b64encode(shot.path.read_bytes()).decode("ascii")
    tool_schema = [{
        "type": "function",
        "function": {
            "name": "get_foreground_window",
            "description": "Получить активное окно Windows",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]
    results = {"screenshot": {"title": shot.foreground["title"], "size": [shot.width, shot.height]}}
    try:
        provider.unload_model(brain)
        provider.unload_model(vision)
        started = perf_counter()
        mode_a = {
            "brain_cold": run_chat(brain, "Кратко ответь по-русски: чем полезен локальный голосовой агент?"),
            "brain_warm": run_chat(brain, "Ответь одним предложением: ты готов работать локально?"),
            "tool_call": run_chat(brain, "Обязательно используй инструмент и узнай активное окно.", tool_schema),
            "brain_vram": gpu(),
        }
        provider.unload_model(brain)
        mode_a["vision_cold"] = run_chat(vision, "Прочитай уникальную строку в окне. Ответь точно.", image=encoded)
        mode_a["vision_warm"] = run_chat(vision, "Какое приложение показано и какой текст виден?", image=encoded)
        mode_a["vision_vram"] = gpu()
        provider.unload_model(vision)
        mode_a["sequence_total_seconds"] = perf_counter() - started
        results["mode_a"] = mode_a

        started = perf_counter()
        mode_b = {
            "brain_cold": run_chat(vision, "Кратко ответь по-русски: чем полезен локальный голосовой агент?"),
            "brain_warm": run_chat(vision, "Ответь одним предложением: ты готов работать локально?"),
            "tool_call": run_chat(vision, "Обязательно используй инструмент и узнай активное окно.", tool_schema),
            "vision_warm": run_chat(vision, "Прочитай уникальную строку в окне. Ответь точно.", image=encoded),
            "vision_second": run_chat(vision, "Какое приложение показано и какой текст виден?", image=encoded),
            "combined_vram": gpu(),
        }
        mode_b["sequence_total_seconds"] = perf_counter() - started
        results["mode_b"] = mode_b
    finally:
        shot.cleanup()
        provider.unload_model(brain)
        provider.unload_model(vision)
    output = ROOT / "data" / "vision_benchmark.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
