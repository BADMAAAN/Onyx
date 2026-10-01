"""Backend-owned settings. Audio/hotkey changes take effect on orderly restart."""
import json
import os
from pathlib import Path
from threading import RLock

class DesktopSettings:
    def __init__(self, root):
        self.path=Path(root)/'data'/'desktop-settings.json'
        self.lock=RLock()
        try:self.values=json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:self.values={}
        self.values.setdefault('internet','off')
        if self.values.get('schema',1)!=1:raise ValueError('Unsupported backend settings schema')

    def get(self,key,default=None):
        with self.lock:return self.values.get(key,default)

    def save(self,key,value):
        if key in ('wake_enabled','observation_paused'):
            if type(value) is not bool:raise ValueError('Expected boolean')
        elif key=='retention_days':
            if type(value) is not int or not 1<=value<=30:raise ValueError('Retention must be 1–30 days')
        elif key=='excluded_apps':
            if not isinstance(value,str) or len(value)>2000:raise ValueError('Invalid exclusions')
            value=[s.strip().casefold() for s in value.split(',') if s.strip()]
            if any(not __import__('re').fullmatch(r'[\w .-]+\.exe',s) for s in value):raise ValueError('Use comma-separated executable names')
        elif key=='internet':
            if value not in ('off','explicit','auto'):raise ValueError('Invalid Internet mode')
        elif key=='chat_default':
            from .model_management import model_name
            value=model_name(value)
        elif key=='git_project':
            value=str(Path(value).resolve(strict=True))
            if not Path(value).is_dir():raise ValueError('Choose a project directory')
        elif key in ('microphone','output_device'):
            from kk.voice.audio_devices import list_audio_devices
            direction='input_channels' if key=='microphone' else 'output_channels'
            devices=list_audio_devices()
            if value and not any(d[direction]>0 and device_identity(d)==value for d in devices):
                raise ValueError('Audio device is no longer available')
        elif key=='show_hotkey':
            import keyboard
            if not isinstance(value,str) or len(value)>50:raise ValueError('Invalid hotkey')
            if value:
                chords=lambda v:{tuple(sorted(c)) for step in keyboard.parse_hotkey_combinations(v) for c in step}
                from kk.config import Config
                config=Config.load(self.path.parent.parent/'config.toml')
                reserved=set().union(*(chords(x) for x in (config.get('voice.push_to_talk_hotkey','ctrl+space'),'esc','ctrl+alt+pause')))
                if chords(value)&reserved:raise ValueError('Hotkey conflicts with Onyx recording or Stop')
        else:raise ValueError('Unknown backend setting')
        with self.lock:
            candidate=dict(self.values,**{key:value},schema=1)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp')
            with temporary.open('w',encoding='utf-8') as stream:
                json.dump(candidate,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
            self.values=candidate
        return dict(values=candidate,restart_required=key in ('microphone','output_device','show_hotkey','wake_enabled','retention_days','excluded_apps'))

def device_identity(device):
    return '@'+device['hostapi']+'|'+device['name']

def snapshot(config,loop,settings):
    from kk.voice.audio_devices import list_audio_devices
    devices=list_audio_devices()
    return dict(values=dict(settings.values),
        inputs=[dict(identity=device_identity(d),label=d['name']+' · '+d['hostapi']) for d in devices if d['input_channels']>0],
        outputs=[dict(identity=device_identity(d),label=d['name']+' · '+d['hostapi']) for d in devices if d['output_channels']>0],
        configured_input=config.get('voice.microphone',''),configured_output=config.get('voice.output_device',''),
        voice=getattr(loop.tts,'actual_voice',''),stt=config.get('voice.stt_model',config.get('voice.whisper_model','faster-whisper (configured backend)')),show_hotkey=config.get('desktop.show_hotkey',''),
        wake_enabled=bool(loop.wakeword),observation_paused=settings.get('observation_paused',False),
        retention_days=settings.get('retention_days',7),excluded_apps=settings.get('excluded_apps',config.get('context.excluded_apps',[])),
        hotkey_limit='Conflicts with other applications cannot be detected by the current hotkey backend.')
