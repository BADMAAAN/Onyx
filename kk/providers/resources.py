"""Serialize Ollama leases and reserve room for Windows, desktop apps and STT."""
from contextlib import contextmanager
from threading import RLock, Thread, Event
from time import monotonic
import subprocess
import psutil
from .http import request_json


@contextmanager
def process_model_lock(base_url, cancellation=None):
    """OS-owned lease shared by background runtime and diagnostic processes."""
    import hashlib
    import msvcrt
    from pathlib import Path
    from tempfile import gettempdir
    from time import sleep
    path=Path(gettempdir())/('onyx-model-'+hashlib.sha256(base_url.encode()).hexdigest()[:16]+'.lock')
    with path.open('a+b') as handle:
        if path.stat().st_size == 0: handle.write(b'0'); handle.flush()
        deadline=monotonic()+30
        while True:
            if cancellation: cancellation.check()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:
                if monotonic()>deadline: raise ResourcePressure('Another Onyx process owns the model lease')
                sleep(.1)
        try: yield
        finally:
            handle.seek(0); msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


class ResourcePressure(MemoryError):
    pass


def available_vram(state):
    # Driver-reserved memory is neither used nor available. Older snapshots
    # predate memory.free, so keep their total-minus-used compatibility path.
    if 'vram_free' in state:
        return state['vram_free']
    return state['vram_total'] - state['vram_used']


def responsiveness():
    """Bounded read-only WM_NULL round trip to the foreground message queue."""
    import ctypes
    from ctypes import wintypes
    started = monotonic()
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd: return {'responsive':True, 'milliseconds':0, 'basis':'no foreground window'}
    result = ctypes.c_size_t()
    send = ctypes.windll.user32.SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    send.restype = ctypes.c_size_t
    ok = bool(send(hwnd, 0, 0, 0, 0x0002, 150, ctypes.byref(result)))
    return {'responsive':ok, 'milliseconds':(monotonic()-started)*1000,
            'basis':'foreground WM_NULL / 150ms timeout, not pointer-to-photon'}


class PressureGuard:
    def __init__(self, manager, model):
        self.manager, self.model = manager, model
        self.done, self.aborted = Event(), Event()
        self.reason = ''
        self.samples = []
        self.abort_io = None
        self.swap_start = psutil.swap_memory().used
        self.disk_before = psutil.disk_io_counters()
        self.previous = monotonic()
        self.unresponsive = 0
        self.thread = Thread(target=self.watch, name='OnyxResourceGuard', daemon=True)
        self.last_models_check = 0

    def check(self):
        if self.aborted.is_set(): raise ResourcePressure(self.reason)

    def fail(self, reason):
        self.reason = reason
        self.manager._set_pressure(True)
        self.aborted.set()
        if self.abort_io:
            try: self.abort_io()
            except OSError: pass

    def watch(self):
        try:
            self._watch()
        except Exception as exc:
            self.fail('Resource telemetry failed: '+str(exc))

    def _watch(self):
        while not self.done.wait(.5):
            ram, swap = psutil.virtual_memory(), psutil.swap_memory()
            now = monotonic()
            interval = now-self.previous
            disk = psutil.disk_io_counters()
            traffic = ((disk.read_bytes+disk.write_bytes-self.disk_before.read_bytes-self.disk_before.write_bytes) /
                       max(.001, now-self.previous)) if disk and self.disk_before else 0
            self.disk_before, self.previous = disk, now
            try: responsive = responsiveness()
            except Exception as exc:
                self.fail('Desktop telemetry failed: '+str(exc))
                return
            self.unresponsive = 0 if responsive['responsive'] else self.unresponsive+1
            row = dict(ram_available=ram.available, ram_percent=ram.percent, swap_used=swap.used,
                       swap_growth=swap.used-self.swap_start, disk_bytes_per_second=traffic,
                       watchdog_interval_seconds=interval, **responsive)
            self.samples.append(row)
            self.samples = self.samples[-2400:]
            if ram.available < self.manager.ram_reserve + 1024**3:
                self.manager._set_pressure(True)
            reason = None
            if ram.available < self.manager.ram_reserve: reason = 'RAM reserve below 8 GB'
            elif ram.percent >= 90: reason = 'RAM usage >=90%'
            elif swap.used-self.swap_start > 256*1024**2: reason = 'Pagefile growth >256 MB during inference'
            elif ram.available < self.manager.ram_reserve+1024**3 and traffic > 150*1024**2:
                reason = 'Memory pressure with disk traffic >150 MB/s'
            elif self.unresponsive >= 2: reason = 'Foreground unresponsive in two consecutive 150ms probes'
            elif interval > 3: reason = 'Resource watchdog starved for >3 seconds; desktop responsiveness at risk'
            if not reason and now-self.last_models_check > 2:
                self.last_models_check = now
                try:
                    loaded=request_json(self.manager.provider.base_url+'/api/ps',timeout=.5).get('models',[])
                    row['resident_models']=[m.get('name') or m.get('model') for m in loaded]
                    if len(loaded)>1: reason='More than one Ollama model resident during inference'
                    if any(m.get('size',0)-m.get('size_vram',0)>1024**3 for m in loaded):
                        reason='Observed RAM offload exceeds 1 GB during inference'
                    gpu=self.manager.snapshot()
                    row.update({key: value for key, value in gpu.items()
                                if key.startswith('vram_')})
                    if 'vram_total' in gpu and available_vram(gpu) < self.manager.vram_reserve + 512*1024**2:
                        self.manager._set_pressure(True)
                    if 'vram_total' in gpu and available_vram(gpu) < self.manager.vram_reserve:
                        reason='VRAM headroom below configured reserve during inference'
                except Exception:
                    # A loading backend can briefly delay ps. RAM sampling remains
                    # independent, and preflight already required valid telemetry.
                    row['model_telemetry_delayed']=True
            if reason:
                self.fail(reason)
                return


def managed_model(method):
    from functools import wraps
    @wraps(method)
    def call(self, model, messages, *args, **kwargs):
        registry=getattr(self,'model_manager',None)
        if registry:registry.verify(model)
        manager = getattr(self, 'resource_manager', None)
        if manager is None:
            return method(self, model, messages, *args, **kwargs)
        role = 'vision' if any(m.get('images') for m in messages) else 'brain'
        with manager.lease(model, role, cancellation=kwargs.get('cancellation')):
            return method(self, model, messages, *args, **kwargs)
    return call


class ModelResourceManager:
    def __init__(self, provider, ram_reserve_gb=8, stt_vram_gb=2, vram_reserve_gb=1):
        self.provider = provider
        self.ram_reserve = int(max(8,ram_reserve_gb) * 1024**3)
        self.stt_vram = int(stt_vram_gb * 1024**3)
        self.vram_reserve = int(vram_reserve_gb * 1024**3)
        self.lock = RLock()
        self.warm_states = {}
        self.last_snapshot = {}
        self.guard = None
        self.last_health = {}
        self.unsuitable = {}
        self.transient_failures = {}
        self.recovery_cooldown_seconds = 10
        self.known_sizes = {}
        self.model_profiles = {}
        self.idle_stop = Event()
        self.on_pressure = lambda value: None

    def _set_pressure(self, value):
        # Visual degradation is advisory; a renderer callback cannot break a
        # model lease or weaken the independent reserve checks.
        try:
            self.on_pressure(bool(value))
        except Exception:
            import logging
            logging.getLogger('kk').exception('Resource visual-pressure callback failed')

    def start_idle_watchdog(self):
        def monitor():
            while not self.idle_stop.wait(2):
                if psutil.virtual_memory().available >= self.ram_reserve+1024**3: continue
                if not self.lock.acquire(blocking=False): continue
                try:
                    if self.guard: continue
                    with process_model_lock(self.provider.base_url):
                        for name,state in list(self.warm_states.items()):
                            if state.get('state')=='warm':
                                self.ensure_unloaded(name)
                                self.warm_states[name]={'state':'unloaded','reason':'idle RAM pressure','at':monotonic()}
                except Exception:
                    import logging
                    logging.getLogger('kk').exception('Idle model unloading failed')
                finally: self.lock.release()
        Thread(target=monitor,name='OnyxIdleResources',daemon=True).start()

    def prepare_stt(self):
        with self.lock, process_model_lock(self.provider.base_url):
            state=self.snapshot()
            if state.get('backend_error') or 'vram_total' not in state:
                details = '; '.join(str(state[key]) for key in ('backend_error', 'gpu_error') if state.get(key))
                raise ResourcePressure('Cannot verify resources before STT loading' + (': ' + details if details else ''))
            for model in state['models']:
                self.ensure_unloaded(model.get('name') or model.get('model'))
            state=self.snapshot()
            if state.get('backend_error') or 'vram_total' not in state or state['models']:
                raise ResourcePressure('STT preflight could not confirm clean model residency')
            if available_vram(state) < self.stt_vram+self.vram_reserve:
                raise ResourcePressure('Insufficient VRAM headroom for STT loading')
            if psutil.virtual_memory().available < self.ram_reserve+2*1024**3:
                raise ResourcePressure('STT loading would violate Windows RAM headroom')

    def prepare_tts(self):
        state=self.snapshot()
        if state.get('backend_error') or 'vram_total' not in state:
            raise ResourcePressure('Cannot verify resources before TTS loading')
        if state['ram_available'] < self.ram_reserve+2*1024**3:
            raise ResourcePressure('TTS loading would violate Windows RAM headroom')

    def snapshot(self):
        ram = psutil.virtual_memory()
        result = {'ram_total': ram.total, 'ram_available': ram.available,
                  'stt_vram_reserved': self.stt_vram, 'models': []}
        try:
            result['models'] = request_json(self.provider.base_url + '/api/ps', timeout=5).get('models', [])
        except Exception as exc:
            result['backend_error'] = str(exc)
        try:
            # Voice dependencies can leave a non-duplicable Windows stderr
            # handle in pythonw. Own the probe handles even in redirected
            # background launches; telemetry must not depend on the parent UI.
            values = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.total,memory.used,memory.free',
                '--format=csv,noheader,nounits'], text=True, timeout=3,
                stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).splitlines()[0].split(',')
            result.update(vram_total=int(values[0])*1024**2, vram_used=int(values[1])*1024**2,
                          vram_free=int(values[2])*1024**2)
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            result['gpu_error'] = str(exc)
        self.last_snapshot = result
        return result

    def ensure_unloaded(self, model):
        self.provider.unload_model(model)
        deadline = monotonic()+10
        while monotonic() < deadline:
            rows = request_json(self.provider.base_url+'/api/ps',timeout=3).get('models',[])
            if not any((r.get('name') or r.get('model')) == model for r in rows): return
            from time import sleep
            sleep(.1)
        raise ResourcePressure('Model unload not confirmed; refusing the next load')

    def preflight(self, model):
        state = self.snapshot()
        if state.get('backend_error') or 'vram_total' not in state:
            raise ResourcePressure('RAM/VRAM/Ollama telemetry unavailable; loading refused')
        if model in self.unsuitable:
            raise ResourcePressure('Candidate excluded from this runtime: '+self.unsuitable[model])
        recent = self.transient_failures.get(model)
        if recent and monotonic() - recent['at'] < self.recovery_cooldown_seconds:
            raise ResourcePressure('Resource recovery cooldown: ' + recent['reason'])
        if state['ram_available'] < self.ram_reserve+1024**3:
            raise ResourcePressure('Need at least 9 GB available RAM before model loading (8 GB reserve + headroom)')
        if not responsiveness()['responsive']:
            raise ResourcePressure('Desktop is unresponsive before loading; benchmark refused')
        models = {m.get('name') or m.get('model'):m for m in self.provider.list_models()}
        if model not in models: raise ResourcePressure('Requested model is not installed: '+model)
        loaded = next((r for r in state['models'] if (r.get('name') or r.get('model'))==model), None)
        context = self.provider.context_window or 8192
        # Cold loads and context changes retain their conservative projection.
        # An observation at a smaller context cannot price a larger allocation.
        estimate = self.known_sizes.get((model, context), int(models[model].get('size',0)*1.05 +
                                    context*96*1024))
        profile = self.model_profiles.get(model)
        if profile and profile.get('context') == context:
            estimate = int(profile['resident_gb']*1024**3)
        existing = loaded.get('size_vram',0) if loaded else 0
        if loaded and loaded.get('context_length') == context and existing > 0:
            # The current GPU sample already includes this exact allocation.
            # Charging the cold-load profile again invents extra VRAM pressure.
            if loaded.get('size', 0) - existing > 1024**3:
                raise ResourcePressure('Observed RAM offload exceeds 1 GB')
            estimate = existing
        budget = available_vram(state) + existing - self.vram_reserve
        offload = max(0,estimate-budget)
        if offload:
            import logging
            logging.getLogger('kk').warning(
                '[RESOURCE] preflight rejected model=%s context=%s estimate=%s free=%s existing=%s reserve=%s ram_available=%s',
                model, context, estimate, available_vram(state), existing, self.vram_reserve, state['ram_available'])
            raise ResourcePressure('Model would consume the VRAM reserve; unsuitable for interactive runtime')
        if state['ram_available']-offload-2*1024**3 < self.ram_reserve:
            raise ResourcePressure('Projected model working memory would violate RAM reserve')
        self.transient_failures.pop(model, None)
        state.update(projected_model_bytes=estimate,projected_offload_bytes=offload)
        return state

    @contextmanager
    def lease(self, model, role='brain', cancellation=None):
        waiting = monotonic()
        while not self.lock.acquire(timeout=.1):
            if cancellation: cancellation.check()
        try:
            with process_model_lock(self.provider.base_url, cancellation):
                if cancellation:
                    cancellation.mark('resource_lease_acquired', model=model, role=role,
                                      wait_seconds=monotonic()-waiting)
                with self._lease_locked(model, role, cancellation) as state:
                    yield state
        finally:
            self._set_pressure(False)
            self.lock.release()

    @contextmanager
    def _lease_locked(self, model, role='brain', cancellation=None):
            if cancellation: cancellation.check()
            state = self.snapshot()
            if cancellation: cancellation.check()
            if state.get('backend_error'):
                raise ResourcePressure('Cannot inspect loaded Ollama models; loading refused')
            # One Ollama model at a time. STT stays resident; model metadata is
            # re-read after every service restart instead of trusting a cache.
            for loaded in state['models']:
                if cancellation: cancellation.check()
                name = loaded.get('name') or loaded.get('model')
                if name and name != model:
                    unloading = monotonic()
                    self.ensure_unloaded(name)
                    if cancellation:
                        cancellation.mark('model_unloaded', model=name, seconds=monotonic()-unloading)
                    self.warm_states[name] = {'state': 'unloaded', 'at': monotonic()}
                    if cancellation: cancellation.check()
            state = self.preflight(model)
            if cancellation: cancellation.check()
            was_resident = any((row.get('name') or row.get('model')) == model for row in state['models'])
            if cancellation:
                cancellation.mark('model_request_state', model=model, role=role,
                                  residency='warm' if was_resident else 'cold')
            guard = PressureGuard(self,model)
            self.guard = guard
            guard.thread.start()
            self.warm_states[model] = {'state': 'loading_or_warm', 'role': role, 'at': monotonic()}
            try:
                if cancellation: cancellation.check()
                yield state
                guard.check()
                for row in self.snapshot().get('models',[]):
                    if (row.get('name') or row.get('model'))==model:
                        if row.get('context_length') and row.get('size', 0) > 0:
                            self.known_sizes[(model, row['context_length'])] = row['size']
                        if row.get('size',0)-row.get('size_vram',0)>1024**3:
                            guard.fail('Observed RAM offload exceeds 1 GB')
                            guard.check()
                self.warm_states[model] = {'state': 'warm', 'role': role, 'at': monotonic()}
            finally:
                guard.done.set(); guard.thread.join(1)
                self.last_health={'model':model,'safe':not guard.aborted.is_set(), 'reason':guard.reason,'samples':guard.samples}
                if guard.aborted.is_set():
                    import logging
                    logging.getLogger('kk').warning('[RESOURCE] inference aborted model=%s reason=%s last_sample=%s',
                        model, guard.reason, guard.samples[-1] if guard.samples else {})
                self.guard = None
                if guard.aborted.is_set():
                    if 'offload exceeds' in guard.reason:
                        self.unsuitable[model] = guard.reason
                    else:
                        self.transient_failures[model] = {'reason': guard.reason, 'at': monotonic()}
                incomplete=self.warm_states.get(model,{}).get('state')=='loading_or_warm'
                if role == 'vision' or guard.aborted.is_set() or (incomplete and not was_resident):
                    unloading = monotonic()
                    self.ensure_unloaded(model)
                    if cancellation:
                        cancellation.mark('model_unloaded', model=model, seconds=monotonic()-unloading)
                    self.warm_states[model] = {'state': 'unloaded', 'at': monotonic()}
                elif incomplete:
                    # Cancelling a turn or rejecting malformed output must not
                    # cold-start a healthy model that was already resident.
                    self.warm_states[model] = {'state': 'warm', 'role': role, 'at': monotonic()}
                guard.check()
