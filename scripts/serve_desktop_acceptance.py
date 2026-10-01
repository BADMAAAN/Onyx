"""Real desktop/Brain transport in an isolated root without opening audio.

Uses the production runtime, VoiceLoop text worker, model manager, SQLite and
HTTP control. Never calls VoiceLoop.run, starts ambient sampling, warms speech
models or opens a microphone. Shutdown affects only this owned server.
"""
import argparse
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    project = Path(__file__).resolve().parents[1]
    from onyx_desktop.acceptance_scope import validate_acceptance
    validate_acceptance(SimpleNamespace(production_acceptance_dir=str(root/'evidence'), backend_root=str(root), visual_test=False), root, project)
    from kk.config import Config
    from kk.core.instance import SingleInstance
    from kk.core.runtime_identity import startup_identity
    from kk.logging_setup import configure_logging
    from kk.runtime import build_runtime
    from kk.voice.factory import build_voice_services
    from kk.core.control import RuntimeControl
    config = Config.load(root/'config.toml')
    config.values.setdefault('wakeword', {})['mode'] = 'disabled'
    config.values.setdefault('voice', {}).update(tts_muted=True, ducking_enabled=False, aec_enabled=False)
    logger = configure_logging(root/'logs', False)
    stopped = Event()
    instance = SingleInstance(root/'data/kk.lock')
    runtime = voice = server = None
    try:
        runtime = build_runtime(config, logger, allow_offline=True)
        runtime.build = dict(startup_identity(project, root/'config.toml'), data_root=str(root), acceptance_no_audio=True)
        voice = build_voice_services(config, runtime)
        voice.loop.startup_phase = 'text-only acceptance'
        server = RuntimeControl.background(config, runtime, voice, stopped.set)
        server.start()
        print(json.dumps(dict(pid=server.metadata['pid'], root=str(root), identity=server.metadata['identity'], acceptance_no_audio=True)), flush=True)
        stopped.wait()
    finally:
        if server: server.close()
        if voice:
            voice.loop.stop()
            if voice.loop.worker: voice.loop.worker.join(5)
            voice.loop.speech.close()
            if voice.loop.audio_focus: voice.loop.audio_focus.close()
        if runtime:
            manager = getattr(runtime.provider, 'resource_manager', None)
            if manager: manager.idle_stop.set()
            runtime.context.stop()
            runtime.memory.close()
        instance.close()


if __name__ == '__main__': main()
