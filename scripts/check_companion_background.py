"""Bounded tray/Orb/physical capture smoke. Wake phrase is disabled for this run."""
from pathlib import Path
import logging
import sys
import json
from threading import Thread,Event
from time import monotonic
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))

def main():
    from kk.config import Config
    from kk.runtime import build_runtime
    from kk.voice.factory import build_voice_services
    from kk.ui.tray import TrayController
    from kk.permissions.policy import PermissionLevel
    from scripts.benchmark_companion_stt import resources
    import psutil
    config=Config.load(ROOT/'config.toml')
    config.values['wakeword']['phrase']='acceptance_disabled_activation_732912'
    runtime=build_runtime(config,logging.getLogger('background-check'))
    runtime.policy.autonomy=PermissionLevel.READ
    voice=build_voice_services(config,runtime)
    runtime.context.sample(); runtime.context.start()
    stop=Event(); samples=[]; process=psutil.Process(); process.cpu_percent()
    def monitor():
        while not stop.wait(1):
            row=resources(); row.update(state=runtime.state.state.value,cpu_percent=process.cpu_percent(),time=monotonic())
            samples.append(row)
    thread=Thread(target=monitor,daemon=True); thread.start()
    try: TrayController(config,runtime,voice).run(22)
    finally:
        stop.set(); thread.join(6); voice.loop.stop(); voice.loop.speech.close()
        voice.loop.audio_focus.close(); runtime.context.stop(); runtime.memory.close()
    output=dict(source='real background/tray/physical microphone; wake activation disabled for automated check',
                samples=samples,states=sorted({s['state'] for s in samples}),
                error_states=sum(s['state']=='ERROR' for s in samples))
    (ROOT/'data/companion_background_acceptance.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(output,indent=2))

if __name__=='__main__': main()
