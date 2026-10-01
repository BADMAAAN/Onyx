"""GSMTC controls. Session selection never falls back to global media keys."""
import asyncio


class MediaController:
    def __init__(self):
        self.last_session = None
        self.audio_muted = {}

    async def _operate(self, action, session_id=None):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        manager = await Manager.request_async()
        sessions = list(manager.get_sessions())
        rows = []
        for session in sessions:
            info = session.get_playback_info()
            props = await session.try_get_media_properties_async()
            rows.append(dict(session_id=session.source_app_user_model_id,
                title=props.title, artist=props.artist, status=int(info.playback_status),
                playing=int(info.playback_status) == 4))
        if not sessions:
            return self._audio(action, session_id)
        if session_id and session_id.startswith('audio:'):
            return self._audio(action, session_id)
        if action == 'list':
            # Electron players can expose Core Audio while another browser owns
            # a GSMTC session. Neither source should hide the other.
            return rows + self._audio('list')
        if action in ('resume', 'play_pause') and not session_id:
            session_id = self.last_session
        matches = [s for s in sessions if s.source_app_user_model_id == session_id] if session_id else []
        if not session_id:
            playing = [s for s, row in zip(sessions, rows) if row['playing']]
            if len(playing) > 1:
                raise ValueError('Several media sources are playing; ask which session to control')
            matches = playing or ([manager.get_current_session()] if manager.get_current_session() else [])
        if len(matches) != 1:
            raise LookupError('Media session missing or ambiguous; get_media_sessions and ask the user')
        session = matches[0]
        selected = session.source_app_user_model_id
        row = next(r for r in rows if r['session_id'] == selected)
        if action == 'info': return row
        methods = dict(pause='try_pause_async', resume='try_play_async',
            play_pause='try_toggle_play_pause_async', next='try_skip_next_async', previous='try_skip_previous_async')
        success = await getattr(session, methods[action])()
        if not success: raise RuntimeError('Media application rejected control')
        self.last_session = selected
        expected_status = {'pause':5,'resume':4}.get(action)
        status = int(session.get_playback_info().playback_status)
        if expected_status is not None:
            for _ in range(10):
                if status == expected_status: break
                await asyncio.sleep(.05)
                status = int(session.get_playback_info().playback_status)
        verified=status==expected_status if expected_status else False
        if action in ('next','previous'):
            for _ in range(20):
                current=await session.try_get_media_properties_async()
                if (current.title,current.artist)!=(row['title'],row['artist']):
                    verified=True; break
                await asyncio.sleep(.1)
        return dict(session_id=selected, requested=action, accepted=True,
                    status=status, playing=status == 4, verified=verified)

    def call(self, action, session_id=None):
        import comtypes
        comtypes.CoInitialize()
        try:
            if action in ('resume','play_pause') and self.last_session and self.last_session.startswith('audio:'):
                return self._audio(action, session_id or self.last_session)
            try: return asyncio.run(self._operate(action, session_id))
            except (ImportError, OSError): return self._audio(action, session_id)
        finally: comtypes.CoUninitialize()

    def _audio(self, action, session_id=None):
        from kk.voice.audio_focus import CoreAudioSessions
        import os
        sessions = CoreAudioSessions().sessions()
        rows = [dict(session_id='audio:'+key,process=s['process'],volume=s['volume'],muted=s['muted'],
                     peak=s['peak'],active=s['active'],
                     method='Core Audio mute fallback; transport unavailable')
                for key,s in sessions.items() if s['pid'] not in (0,os.getpid()) and s['active']]
        if action == 'list': return rows
        if session_id:
            matches=[r for r in rows if r['session_id']==session_id]
        else:
            matches=[r for r in rows if sessions[r['session_id'][6:]]['peak']>.001]
        if len(matches)!=1: raise ValueError('Audio source missing or ambiguous; ask which process')
        row=matches[0]; key=row['session_id'][6:]; s=sessions[key]
        if action=='info': return row
        if action in ('next','previous'): raise RuntimeError('This application exposes no track controls')
        if action=='pause' or (action=='play_pause' and not s['muted']):
            self.audio_muted[key]=s['muted']; s['control'].SetMute(True,None)
        elif key in self.audio_muted:
            if s['muted']: s['control'].SetMute(self.audio_muted.pop(key),None)
        else: raise RuntimeError('No previously paused audio session to resume')
        self.last_session=row['session_id']
        return dict(row,requested=action,accepted=True)
