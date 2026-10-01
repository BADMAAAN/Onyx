from time import monotonic, sleep
from kk.tools.registry import Tool
from kk.permissions.policy import PermissionLevel as P
from . import filesystem as fs
from .development import run_terminal, git_status
from .media import MediaController


def register_companion_tools(registry):
    from .factory import obj
    s = {'type': 'string'}
    path = {'path': s}
    media = MediaController()
    def play_music(application=''):
        registry.check_cancel()
        rows = media.call('list')
        decision = getattr(registry, 'decision_policy', None)
        preferred = decision.preferences.get('music_app', 'Яндекс Музыка') if decision else 'Яндекс Музыка'
        if application: preferred=application
        from .app_resolver import canonical
        desired = canonical(preferred)
        def matching(rows):
            matches = [r for r in rows if
                (desired in canonical(r.get('session_id','')+' '+r.get('process','')) or
                 (desired == 'yandexmusic' and any(x in
                  (r.get('session_id','')+' '+r.get('process','')).casefold() for x in ('yandex','яндекс'))))]
            transport=[r for r in matches if not r.get('session_id','').startswith('audio:')]
            return transport or matches
        transport = matching(rows)
        if len(transport)==1 and not transport[0].get('session_id','').startswith('audio:'):
            current=transport[0]
            if current.get('playing'):
                return dict(current, verified=True, target_application=preferred)
            try:
                resumed=media.call('resume',current['session_id'])
                if resumed.get('verified'): return dict(resumed, target_application=preferred)
            except (LookupError,ValueError,RuntimeError): pass
        opened = registry.execute('open_application', {'name': preferred})
        if not opened.ok: raise RuntimeError(opened.error)
        deadline = monotonic()+3
        while monotonic() < deadline:
            registry.check_cancel()
            rows = media.call('list')
            matches = matching(rows)
            if len(matches) == 1:
                row = matches[0]
                if row.get('session_id','').startswith('audio:'):
                    # Sound from a process may be a notification, not music.
                    # Only transport playback can confirm this request.
                    sleep(.1)
                    continue
                result = dict(row, verified=True) if row.get('playing') else media.call('resume', row['session_id'])
                return dict(result, target_application=preferred)
            sleep(.1)
        return dict(verified=False, target_application=preferred, reason='Плеер открыт; требуется наблюдение UI для начала воспроизведения', window=opened.data)
    from .codex_integration import CodexIntegration
    codex = CodexIntegration(registry)
    def add(name, description, properties, required, permission, handler):
        registry.register(Tool(name, description, obj(properties, required), permission, handler))
    add('play_music', 'Play music in the preferred player or specified application (e.g. Яндекс Музыка). Uses observed transport/audio; opened window alone is never verified playback.', {'application':s}, [], P.ACTION, play_music)
    add('codex_status', 'Check installed Codex CLI without starting a task.', {}, [], P.READ, codex.status)
    add('codex_last_result', 'Read the last result submitted through KK.', {}, [], P.READ, codex.last)
    add('open_codex', 'Open interactive Codex CLI in an explicitly requested project.', dict(cwd=s), ['cwd'], P.ACTION, codex.open)
    add('run_codex_task', 'Submit a paid Codex task ONLY on a direct user command naming Codex.',
        dict(cwd=s,task=s,timeout={'type':'number','minimum':1,'maximum':600}), ['cwd','task'], P.DANGEROUS, codex.run)
    add('read_file', 'Read a UTF-8 file using an absolute path.', path, ['path'], P.READ,
        lambda path: fs.read_text_file(str(fs.absolute(path))))
    add('get_file_info', 'Read filesystem metadata.', path, ['path'], P.READ, fs.info)
    add('search_files', 'Search filenames below an absolute directory.',
        dict(path, pattern=s, limit={'type':'integer','minimum':1,'maximum':500}), ['path'], P.READ,
        lambda **kw: fs.search(**kw, check=registry.check_cancel))
    for name in ('create_file', 'write_file'):
        add(name, 'Write UTF-8 text; existing files require overwrite=true.',
            dict(path, content=s, overwrite={'type':'boolean'}), ['path','content'], P.DANGEROUS, fs.write)
    add('replace_in_file', 'Replace an exact string only if expected_count matches.',
        dict(path, old=s, new=s, expected_count={'type':'integer','minimum':1}), ['path','old','new'], P.DANGEROUS, fs.replace)
    add('create_directory', 'Create an absolute directory.', path, ['path'], P.ACTION, fs.create_directory)
    for name in ('copy', 'move', 'rename'):
        add(name, 'Transfer a file/directory; never overwrite the destination.',
            dict(source=s, destination=s), ['source','destination'], P.DANGEROUS,
            lambda source, destination, moving=name!='copy': transfer(source, destination, moving))
    def transfer(source, destination, moving):
        result = fs.transfer(source, destination, moving)
        if getattr(registry, 'app_resolver', None): registry.app_resolver.invalidate_metadata(source)
        return result
    def delete(path):
        result = fs.delete(path)
        if getattr(registry, 'app_resolver', None): registry.app_resolver.invalidate_metadata(path)
        return result
    add('delete', 'Send the specified path to Recycle Bin.', path, ['path'], P.DANGEROUS, delete)
    for name in ('open_file', 'open_folder'):
        add(name, 'Open a file/folder with its Windows default application.', path, ['path'], P.ACTION, fs.open_path)
    add('run_terminal', 'Run an explicitly requested development command in a hidden process. Returns stdout, stderr and exit code.',
        dict(command=s, cwd=s, shell={'type':'string','enum':['powershell','cmd']},
             timeout={'type':'number','minimum':1,'maximum':600,'description':'Timeout in SECONDS, e.g. 30. Never milliseconds.'}, environment={'type':'object'}),
        ['command','cwd'], P.DANGEROUS, lambda **kw: run_terminal(**kw, check=registry.check_cancel))
    add('git_status', 'Read Git status without refreshing the index.', dict(cwd=s), ['cwd'], P.READ,
        lambda cwd: git_status(cwd, registry.check_cancel))
    def audio_sessions():
        import comtypes
        from kk.voice.audio_focus import CoreAudioSessions
        comtypes.CoInitialize()
        try: return [{k:v for k,v in row.items() if k!='control'} for row in CoreAudioSessions().sessions().values()]
        finally: comtypes.CoUninitialize()
    add('get_audio_sessions','Read active audio processes, exact volumes, mute and peaks.',{},[],P.READ,audio_sessions)
    def adjust_media_volume(delta, session_id=None):
        import os
        import comtypes
        from kk.voice.audio_focus import CoreAudioSessions
        comtypes.CoInitialize()
        try:
            backend=CoreAudioSessions(); sessions=backend.sessions()
            matches=[key for key,row in sessions.items() if row['pid'] not in (0,os.getpid()) and
                ((session_id and session_id.removeprefix('audio:')==key) or
                 (not session_id and row['active'] and not row['muted'] and row['peak']>.001))]
            if len(matches)!=1: raise ValueError('Audio source missing or ambiguous; select session_id from get_audio_sessions')
            key=matches[0]; focus=getattr(registry,'audio_focus',None)
            if focus: return focus.transaction.adjust(key,delta)
            target=max(0,min(1,sessions[key]['volume']+delta/100)); backend.set(sessions[key],target)
            actual=backend.sessions()[key]['volume']
            return dict(session_id=key,percent=round(actual*100),verified=abs(actual-target)<.01)
        finally: comtypes.CoUninitialize()
    add('adjust_media_volume','Change one Core Audio session by percentage points; preserves ducking and new restore level.',
        dict(delta={'type':'integer','minimum':-100,'maximum':100},session_id=s), ['delta'], P.ACTION, adjust_media_volume)
    def network_info():
        import psutil
        return {name:dict(up=s.isup,speed_mbps=s.speed,mtu=s.mtu) for name,s in psutil.net_if_stats().items()}
    add('get_network_info','Read network adapter link state without network requests.',{},[],P.READ,network_info)
    add('get_media_sessions', 'List Windows media sessions, titles and playback status.', {}, [], P.READ, lambda: media.call('list'))
    for name, action in [('get_current_media','info'), ('get_media_info','info'), ('pause_media','pause'),
                         ('resume_media','resume'), ('play_pause_media','play_pause'),
                         ('next_track','next'), ('previous_track','previous')]:
        add(name, 'Control/query GSMTC media. Ask which source if ambiguous. Resume targets the last controlled session.',
            dict(session_id=s), [], P.READ if action=='info' else P.ACTION,
            lambda session_id=None, operation=action: media.call(operation, session_id))
