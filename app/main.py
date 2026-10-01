from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import os

from kk.config import Config
from kk.logging_setup import configure_logging
from kk.runtime import Runtime, build_runtime


ROOT = Path(__file__).resolve().parents[1]


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Onyx Personal Agent")
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml")
    parser.add_argument("--once", help="Выполнить один текстовый запрос и завершиться")
    parser.add_argument('--say', help='Передать текст в очередь работающего Onyx и озвучить полный ответ')
    parser.add_argument('--status', action='store_true', help='Статус единственного фонового Onyx')
    parser.add_argument('--shutdown', action='store_true', help='Штатно завершить фоновый Onyx')
    parser.add_argument('--pause-input', action='store_true', help='Приостановить микрофон работающего Onyx')
    parser.add_argument('--resume-input', action='store_true', help='Возобновить микрофон работающего Onyx')
    parser.add_argument('--console-client', action='store_true', help='Консоль ввода для уже работающего Onyx')
    parser.add_argument("--doctor", action="store_true", help="Проверить backend, tools, screen и memory")
    parser.add_argument("--context", action="store_true", help="Показать Ambient Context inspector без LLM")
    parser.add_argument('--acceptance',action='store_true',help='Интерактивный live microphone acceptance')
    parser.add_argument('--voice-ux-acceptance', action='store_true', help='Live final/mic/alias/Orb acceptance with real microphone')
    parser.add_argument('--companion-acceptance',choices=['record','benchmark','interruptions','hallucinations'],
        help='Локальная Focusrite acceptance-сессия без исполнения STT-команд')
    parser.add_argument('--benchmark',action='store_true',help='Локальные измерения perception, Brain, STT и TTS')
    parser.add_argument("--voice", action="store_true", help="Push-to-talk voice mode")
    parser.add_argument("--wakeword", action="store_true", help="Temporary STT-based wake-word mode")
    parser.add_argument("--wakeword-calibrate", action="store_true", help="Записать real positive/hard-negative wake-word samples")
    parser.add_argument("--wakeword-delete-samples", action="store_true", help="Удалить локальные wake-word calibration samples")
    parser.add_argument("--background", action="store_true", help="System tray/background mode")
    parser.add_argument("--background-test-seconds", type=float, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--list-audio", action="store_true", help="Показать audio input/output devices")
    parser.add_argument("--voice-file", type=Path, help="Распознать WAV, передать Brain и озвучить ответ")
    parser.add_argument("--tts-test", help="Озвучить тестовый русский текст")
    parser.add_argument('--voice-preview', action='store_true', help='Последовательно прослушать все локальные голоса')
    parser.add_argument('--voice-name', choices=['xenia','kseniya','baya','aidar','eugene'], help='Голос для текущего запуска')
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def doctor(runtime: Runtime) -> int:
    print(f"[DOCTOR] provider={runtime.provider.name} model={runtime.model}")
    models = runtime.provider.list_models()
    print(f"[DOCTOR] models={len(models)} tools={len(runtime.tools.names())}")
    answer = runtime.provider.check_inference(runtime.model)
    print(f"[DOCTOR] inference={answer[:160]}")
    foreground = runtime.tools.execute("get_foreground_window").as_dict()
    print("[DOCTOR] foreground=" + json.dumps(foreground, ensure_ascii=False, default=str))
    processes = runtime.tools.execute("get_running_processes").as_dict()
    process_count = len(processes.get("data") or []) if processes.get("ok") else 0
    print(f"[DOCTOR] processes={process_count}")
    shot = runtime.capture.capture("active_window")
    print(f"[DOCTOR] screenshot={shot.width}x{shot.height} temporary={shot.temporary}")
    shot.cleanup()
    runtime.memory.remember("doctor_roundtrip", "ok", "fact", "self-test")
    recalled = runtime.memory.recall("doctor_roundtrip")
    print(f"[DOCTOR] memory_roundtrip={'ok' if recalled else 'failed'}")
    runtime.memory.forget("doctor_roundtrip")
    return 0 if foreground["ok"] and process_count and recalled else 1


def interactive(runtime: Runtime) -> int:
    print(f"Onyx Personal Agent | {runtime.provider.name}:{runtime.model}")
    print("Введите /help для справки, Ctrl+C — Emergency Stop.")
    while True:
        try:
            text = input("User: ").strip()
            if not text:
                continue
            if text in {"/quit", "/exit"}:
                return 0
            if text == "/help":
                print("/status /tools /memory /ambient-pause /ambient-resume /quit")
                continue
            if text in {"/ambient-pause", "/ambient-resume"}:
                print(runtime.context.pause(text == "/ambient-pause"))
                continue
            if text == "/status":
                print(f"provider={runtime.provider.name} model={runtime.model} stop={runtime.policy.stop_file.exists()}")
                continue
            if text == "/tools":
                print("\n".join(runtime.tools.names()))
                continue
            if text == "/memory":
                print(json.dumps(runtime.memory.counts(), ensure_ascii=False))
                continue
            print("Onyx:", runtime.agent.ask(text))
        except KeyboardInterrupt:
            runtime.policy.trigger_stop()
            print("\n[EMERGENCY STOP] Действия Onyx остановлены.")
            return 130


def main() -> int:
    _configure_utf8_console()
    if os.name == 'nt':
        # Native monitor/window geometry and Pillow captures share physical
        # coordinates. Do this before runtime construction or any desktop read.
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass  # Older Windows: backend capability/freshness checks remain.
    args = parse_args()
    config = Config.load(args.config)
    if args.console_client:
        from kk.core.control import request_control
        print('Onyx — подключение к работающему агенту. /exit — закрыть консоль.')
        while True:
            try:
                text = input('User: ').strip()
                if text in {'/exit', '/quit'}: return 0
                if not text: continue
                result = request_control(config.root, 'ask', timeout=config.get('agent.task_timeout_seconds',300)+45,
                                         text=text, spoken=False)
                print('Onyx:', result.get('answer') or result.get('error') or result.get('status'))
            except (EOFError, KeyboardInterrupt):
                return 0
            except Exception as exc:
                print(f'Ошибка: {exc}')
    control_exists = (config.root/'data'/'runtime_control.json').exists()
    if args.status or args.shutdown or args.pause_input or args.resume_input or args.say or (args.once and control_exists):
        from kk.core.control import request_control
        operation, arguments = ('status', {})
        if args.shutdown: operation = 'shutdown'
        elif args.pause_input or args.resume_input:
            operation, arguments = 'microphone', {'paused': args.pause_input}
        elif args.say or args.once:
            operation, arguments = 'ask', {'text': args.say or args.once, 'spoken': bool(args.say)}
        result = request_control(config.root, operation,
            timeout=config.get('agent.task_timeout_seconds', 300)+45 if operation == 'ask' else 10, **arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if not result.get('error') and result.get('status') not in {'FAILED', 'CANCELLED'} else 2
    if args.voice_name:
        config.values.setdefault('voice', {})['tts_voice'] = args.voice_name
    if args.voice_preview:
        from kk.voice.voices import preview
        print(json.dumps(preview(config), ensure_ascii=False, indent=2))
        return 0
    if args.companion_acceptance:
        from scripts import companion_acceptance
        folder = config.root/'data/companion_acceptance/focusrite'
        getattr(companion_acceptance,args.companion_acceptance)(config,folder)
        return 0
    if args.list_audio:
        from kk.voice.audio_devices import list_audio_devices
        print(json.dumps(list_audio_devices(), ensure_ascii=False, indent=2))
        return 0
    if args.wakeword_calibrate or args.wakeword_delete_samples:
        from scripts.train_wakeword import calibrate,delete_calibration
        return calibrate() if args.wakeword_calibrate else delete_calibration()
    logger = configure_logging(config.root / "logs", args.verbose)
    logger.info('[BOOT] pid=%s executable=%s prefix=%s base_prefix=%s cwd=%s config=%s',
                os.getpid(),sys.executable,sys.prefix,sys.base_prefix,os.getcwd(),args.config)
    from kk.core.runtime_identity import startup_identity
    build = startup_identity(ROOT, args.config)
    build['data_root'] = str(config.root.resolve())
    logger.info('[BUILD] commit=%s source_sha256=%s config_sha256=%s',
                build['commit'], build['source_sha256'], build['config_sha256'])
    from kk.core.instance import SingleInstance
    instance = SingleInstance(config.root/'data'/'kk.lock')
    try:
        runtime = build_runtime(config, logger,allow_offline=args.background or args.context or args.voice or args.wakeword or args.voice_ux_acceptance or bool(args.once))
        runtime.build = build
    except Exception:
        instance.close()
        raise
    voice = None
    try:
        if args.context:
            runtime.context.sample(screen=True)
            print(json.dumps(runtime.context.diagnostics(), ensure_ascii=False, indent=2, default=str))
            return 0
        runtime.context.sample()
        runtime.context.start()
        if args.doctor:
            return doctor(runtime)
        if args.once:
            print(runtime.agent.ask(args.once))
            return 0
        if args.benchmark:
            from scripts.benchmark_realtime import run_benchmark
            return run_benchmark(config,runtime)
        if args.voice or args.wakeword or args.background or args.voice_file or args.tts_test or args.acceptance or args.voice_ux_acceptance:
            from kk.voice.factory import build_voice_services
            voice = build_voice_services(config, runtime)
            if args.voice_ux_acceptance:
                from kk.voice.ux_acceptance import run
                return run(config, runtime, voice)
            if args.acceptance:
                from kk.voice.acceptance import run_acceptance
                runtime.context.start()
                return run_acceptance(config,runtime,voice)
            if args.voice_file:
                result = voice.stt.transcribe_final(args.voice_file)
                print(f"[STT] {result.text} ({result.latency_seconds:.2f}s)")
                if not result.accepted:
                    print('[STT] Команда отклонена: '+result.rejection_reason)
                    return 2
                from kk.core.task import CancellationToken
                token = CancellationToken()
                token.voice_evidence = result
                answer = runtime.agent.ask(result.text, cancellation=token)
                print("Onyx:", answer)
                voice.tts.speak(answer)
                return 0
            if args.tts_test:
                latency = voice.tts.speak(args.tts_test)
                print(f"[TTS] first audio start: {latency:.3f}s")
                return 0
            if args.background:
                runtime.context.start()
                from kk.ui.tray import TrayController
                TrayController(config, runtime, voice).run(args.background_test_seconds)
                return 0
            if args.wakeword:
                runtime.context.start()
                voice.loop.wakeword = True
                print('Wake-word: '+('native' if voice.loop.native_wakeword else 'STT fallback')+'; Ctrl+C — стоп')
                try:
                    voice.loop.run()
                except KeyboardInterrupt:
                    voice.loop.stop()
                return 0
            print(f"Voice mode: {voice.loop.hotkey} — запись/commit; Esc — отменить. Ctrl+C — остановить.")
            try:
                voice.loop.run()
            except KeyboardInterrupt:
                voice.loop.stop()
            return 0
        return interactive(runtime)
    finally:
        manager = getattr(runtime.provider, 'resource_manager', None)
        if manager: manager.idle_stop.set()
        runtime.context.stop()
        if voice:
            voice.loop.stop()
            voice.loop.speech.close()
            if voice.loop.audio_focus:
                voice.loop.audio_focus.close()
        runtime.memory.close()
        instance.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        import logging
        logging.getLogger("kk").exception("Fatal Onyx error")
        print(f"[FATAL] {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
