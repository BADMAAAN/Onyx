"""Core Audio session ducking with a write-ahead recovery journal.

Only active, audible sessions are selected. Each fade step compares its last
write with current volume; external changes surrender ownership of that session.
"""
from pathlib import Path
from threading import Thread, Event, RLock
from functools import wraps
from time import sleep
import json
import os

def serialized(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.lock: return method(self,*args,**kwargs)
    return call


class CoreAudioSessions:
    def sessions(self):
        from pycaw.pycaw import AudioUtilities
        from pycaw.utils import AudioSession
        from pycaw.api.audiopolicy import IAudioSessionControl2
        from pycaw.api.endpointvolume import IAudioMeterInformation
        output = {}
        sessions = []
        # eRender=0, DEVICE_STATE_ACTIVE=1: also cover applications routed to a
        # non-default output (Focusrite, monitor audio, headphones).
        for device in AudioUtilities.GetAllDevices(data_flow=0, device_state=1):
            try:
                enumerator = device.AudioSessionManager.GetSessionEnumerator()
                for index in range(enumerator.GetCount()):
                    control = enumerator.GetSession(index).QueryInterface(IAudioSessionControl2)
                    sessions.append((AudioSession(control), device.id))
            except Exception:
                continue
        for session, endpoint_id in sessions:
            try:
                meter = session._ctl.QueryInterface(IAudioMeterInformation)
                output[session.InstanceIdentifier] = dict(
                    id=session.InstanceIdentifier, endpoint_id=endpoint_id, pid=session.ProcessId,
                    process=session.Process.name() if session.Process else 'System Sounds',
                    volume=float(session.SimpleAudioVolume.GetMasterVolume()),
                    muted=bool(session.SimpleAudioVolume.GetMute()),
                    active=session.State == 1, peak=float(meter.GetPeakValue()),
                    control=session.SimpleAudioVolume)
            except Exception:
                continue  # session can disappear between enumeration and access
        return output

    def set(self, session, volume):
        session['control'].SetMasterVolume(float(volume), None)


class AudioFocusTransaction:
    def __init__(self, backend, journal, level=.25, fade_ms=180, own_pid=None):
        self.backend, self.journal = backend, Path(journal)
        self.level = max(0, min(1, level))
        self.steps = max(1, int(fade_ms/20))
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self.owned = {}
        self.lock = RLock()

    def _save(self):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        temp = self.journal.with_suffix('.tmp')
        temp.write_text(json.dumps(self.owned), encoding='utf-8')
        temp.replace(self.journal)

    def recover(self):
        if self.journal.exists():
            self.owned = json.loads(self.journal.read_text(encoding='utf-8'))
            self.restore()

    @serialized
    def duck(self):
        if self.owned:
            return
        for key, s in self.backend.sessions().items():
            if s['pid'] != self.own_pid and s['pid'] != 0 and s['active'] and not s['muted'] and s['peak'] > .001:
                self.owned[key] = dict(original=s['volume'], last=s['volume'], muted=s['muted'])
        self._save()
        self._fade(False)

    def _fade(self, restoring):
        starts = {k: v['last'] for k, v in self.owned.items()}
        for step in range(1, self.steps+1):
            sessions = self.backend.sessions()
            for key, row in list(self.owned.items()):
                session = sessions.get(key)
                # A write-ahead pending value is accepted only after an interrupted write.
                matching = session and any(abs(session['volume']-v) <= .0001
                    for v in (row['last'], row.get('pending', row['last'])))
                if not matching or session['muted'] != row['muted']:
                    self.owned.pop(key, None)
                    self._save()
                    continue
                target = row['original'] if restoring else row['original']*self.level
                value = starts[key]+(target-starts[key])*step/self.steps
                row['pending'] = value
                self._save()  # recover even if process dies immediately after SetMasterVolume
                try:
                    self.backend.set(session, value)
                except Exception:
                    self.owned.pop(key, None)
                    self._save()
                    continue
                row['last'] = value
                row.pop('pending', None)
                self._save()
            if step < self.steps:
                sleep(.02)

    @serialized
    def adjust(self, key, delta):
        session=self.backend.sessions().get(key)
        if session is None: raise LookupError('Audio session disappeared')
        row=self.owned.get(key)
        if row and abs(session['volume']-row['last'])>.0001:
            self.owned.pop(key); row=None
        base=row['original'] if row else session['volume']
        desired=max(0,min(1,base+delta/100))
        target=desired*self.level if row else desired
        if row:
            row.update(original=desired,pending=target); self._save()
        self.backend.set(session,target)
        if row:
            row['last']=target; row.pop('pending',None); self._save()
        observed=self.backend.sessions()[key]['volume']
        return {'session_id':key,'percent':round(desired*100),'output_percent':round(observed*100),
            'ducked':bool(row),'verified':abs(observed-target)<.01}

    @serialized
    def restore(self):
        self._fade(True)
        self.owned.clear()
        self._save()


class AudioFocusManager:
    def __init__(self, journal, enabled=True, level=.25, fade_ms=180):
        self.enabled = enabled
        self.requested = Event()
        self.exit = Event()
        self.error = None
        self.transaction = AudioFocusTransaction(CoreAudioSessions(), journal, level, fade_ms)
        self.thread = Thread(target=self._run, name='KKAudioFocus', daemon=True)
        self.thread.start()

    def acquire(self):
        if self.enabled: self.requested.set()

    def release(self):
        self.requested.clear()

    def _run(self):
        import comtypes
        comtypes.CoInitialize()
        active = False
        try:
            self.transaction.recover()
            while not self.exit.wait(.04):
                requested = self.requested.is_set()
                if requested != active:
                    if requested: self.transaction.duck()
                    else: self.transaction.restore()
                    active = requested
        except Exception as exc:
            self.error = str(exc)
        finally:
            try: self.transaction.restore()
            except Exception as exc: self.error = str(exc)
            comtypes.CoUninitialize()

    def close(self):
        self.requested.clear()
        self.exit.set()
        self.thread.join(4)
