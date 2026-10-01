"""Direct browser navigation followed by address-bar evidence, never launch-only success."""
from urllib.parse import urlparse, unquote
from time import monotonic, sleep
import subprocess
import webbrowser
import re


class BrowserTabs:
    """Read-only adapter for an authorized WebExtensions tabs.query bridge.

    A browser-window title is never a substitute. This installation has a
    known Firefox URL-policy block; no bridge is silently installed or used.
    query_tabs is supplied only by an independently authorized integration.
    """
    POLICY_LIMIT = 'Живая проверка Firefox заблокирована ограничением URL policy; полный список вкладок не получен.'

    def __init__(self, query_tabs=None, *, policy_allowed=False):
        self.query_tabs = query_tabs
        self.policy_allowed = policy_allowed

    def list(self, scope='all_windows', window_id=None):
        from kk.tools.observation import observation
        if scope not in {'all_windows', 'current_window', 'window'}:
            raise ValueError('Unsupported tab scope')
        if scope == 'window' and (type(window_id) is not int or window_id < 0):
            raise ValueError('An explicit browser windowId is required')
        if scope != 'window' and window_id is not None:
            raise ValueError('window_id requires scope=window')
        coverage_scope = {'kind': scope, 'browser': 'firefox'}
        if window_id is not None:
            coverage_scope['windowId'] = window_id
        if not self.policy_allowed:
            result = observation([], source='browser_policy', scope=coverage_scope,
                                 limitations=[self.POLICY_LIMIT])
            return dict(result, status='BLOCKED', verified=False)
        if self.query_tabs is None:
            result = observation([], source='browser_adapter', scope=coverage_scope,
                                 limitations=['Штатный адаптер Firefox Tabs API не подключён.'])
            return dict(result, status='UNVERIFIED', verified=False)
        query = {} if scope == 'all_windows' else (
            {'currentWindow': True} if scope == 'current_window' else {'windowId': window_id})
        # No active/pinned/discarded filter: each would hide requested tabs.
        try:
            packet = self.query_tabs(query)
            return self._normalize(packet, query, coverage_scope)
        except Exception as exc:
            return dict(observation([], source='firefox.tabs.query', scope=coverage_scope,
                limitations=['Ошибка перечисления вкладок: ' + type(exc).__name__]),
                status='UNVERIFIED', verified=False)

    @staticmethod
    def _normalize(packet, query, scope):
        from datetime import datetime, timezone
        from kk.tools.observation import observation
        if not isinstance(packet, dict) or not isinstance(packet.get('tabs'), list):
            raise ValueError('Missing native tab response')
        stamp = datetime.fromisoformat(packet['timestamp'])
        if stamp.tzinfo is None or not -1 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 5:
            raise ValueError('Stale tab observation')
        reasons = []
        if packet.get('query') != query:
            reasons.append('Фильтр источника не совпадает с запрошенной областью.')
        if packet.get('complete') is not True:
            reasons.append('Источник не подтвердил завершение перечисления.')
        if packet.get('private_windows_access') is not True and scope['kind'] == 'all_windows':
            reasons.append('Приватные окна недоступны; перечислены доступные обычные окна.')
        items, identities = [], set()
        for tab in packet['tabs']:
            if any(type(tab.get(k)) is not int or tab[k] < 0 for k in ('windowId', 'id', 'index')):
                raise ValueError('Native tab identity is missing')
            identity = (tab['windowId'], tab['id'])
            if tab['id'] in identities:
                reasons.append('Повторный tabId в ответе источника.')
                continue
            if query.get('windowId') is not None and tab['windowId'] != query['windowId']:
                reasons.append('Источник вернул вкладку за пределами запрошенного окна.')
                continue
            identities.add(tab['id'])
            if not isinstance(tab.get('active'), bool) or not isinstance(tab.get('title'), str):
                reasons.append('Недоступны title/active; проверь разрешение tabs.')
            items.append(dict(windowId=tab['windowId'], tabId=tab['id'], index=tab['index'],
                title=tab.get('title'), active=tab.get('active'), pinned=tab.get('pinned'),
                discarded=tab.get('discarded'), type='browser_tab',
                identity='firefox:%s:%s' % identity, source='firefox.tabs.query',
                verified_at=packet['timestamp']))
        items.sort(key=lambda item: (item['windowId'], item['index']))
        if not items and scope['kind'] in {'window', 'current_window'}:
            reasons.append('Целевое окно не подтверждено ответом источника.')
        for wid in {item['windowId'] for item in items}:
            rows = [item for item in items if item['windowId'] == wid]
            if [item['index'] for item in rows] != list(range(len(rows))):
                reasons.append('Неполная последовательность индексов окна %s.' % wid)
            if sum(item['active'] is True for item in rows) != 1:
                reasons.append('Активная вкладка окна %s не определена однозначно.' % wid)
        if scope['kind'] in {'window', 'current_window'} and len({i['windowId'] for i in items}) > 1:
            reasons.append('Одному целевому окну соответствуют несколько windowId.')
        complete = not reasons
        result = observation(items, source='firefox.tabs.query', scope=scope,
            completeness='complete' if complete else 'partial',
            limitations=list(dict.fromkeys(reasons)), timestamp=packet['timestamp'])
        return dict(result, status='VERIFIED' if complete else 'UNVERIFIED', verified=complete)


class BrowserController:
    """Browser goals use shortcuts/UIA and scoped media evidence."""
    SHORTCUTS = {'new_tab':['ctrl','t'], 'close_tab':['ctrl','w'],
                 'restore_tab':['ctrl','shift','t'], 'first_tab':['ctrl','1'],
                 'back':['alt','left'], 'forward':['alt','right']}

    def __init__(self, registry, uia, media):
        self.registry, self.uia, self.media = registry, uia, media
        self.target_hwnd = None

    def current(self):
        from .native import foreground_window
        window = foreground_window()
        process = window.get('process','').casefold()
        if process not in {'firefox.exe','chrome.exe','msedge.exe','brave.exe','opera.exe'}:
            raise LookupError('Сначала сфокусируй целевой браузер')
        return window

    def evidence(self):
        window = self.current()
        rows = self.uia.tree(False)
        addresses = [str(e.get('value') or e.get('text') or '') for e in rows if address_element(e)]
        # Browser chrome and many tabs can consume the bounded traversal before
        # page content is reached. Read the visible document as its own scope.
        page=self.uia.document_tree()
        rows += page
        return window, rows, addresses

    def playback(self, window, timeout=5):
        deadline = monotonic() + timeout
        process = window.get('process','').removesuffix('.exe').casefold()
        while monotonic() < deadline:
            self.registry.check_cancel()
            if self.target_hwnd and window['hwnd']!=self.target_hwnd:
                return dict(verified=False,playing=False,reason='В фокусе другой браузерный HWND')
            if self.current()['hwnd']!=window['hwnd']:
                return dict(verified=False,playing=False,reason='Целевой браузер потерял фокус')
            rows = self.media.call('list')
            from .native import window_metadata
            current=window_metadata(window['hwnd'])
            addresses=[str(e.get('value') or '') for e in self.uia.tree(False) if address_element(e)]
            on_video=any((urlparse(a if '://' in a else 'https://'+a).hostname or '').removeprefix('www.')=='youtube.com'
                         and re.search(r'/(?:watch\?|shorts/|embed/)',a) for a in addresses)
            # GSMTC belongs to a browser process, not a tab/HWND. Require the
            # playing title in this exact window AND an observed video URL.
            matches=[s for s in rows if process in (s.get('session_id','')+' '+s.get('process','')).casefold()
                     and len(s.get('title',''))>=3 and
                     s['title'].casefold() in current.get('title','').casefold()]
            playing = [s for s in matches if on_video and s.get('playing')]
            if playing:
                return dict(verified=True, playing=True, media_session=playing[0], browser_process=process, hwnd=window['hwnd'], window=current)
            sleep(.15)
        return dict(verified=False, playing=False, hwnd=window['hwnd'], reason='Нет воспроизведения в целевом браузере')

    def control(self, action, query=''):
        from .keyboard import press_hotkey
        from urllib.parse import quote_plus
        if action == 'play_video':
            context=getattr(self.registry,'desktop_context',None)
            browser=getattr(context,'browser','') or 'браузер'
            target='https://www.youtube.com/results?search_query='+quote_plus(query or 'космос')
            navigated=open_url(target,self.registry.app_resolver,browser,self.registry.check_cancel,self.uia)
            if not navigated.get('verified'): return navigated
            self.target_hwnd=navigated['hwnd']
            return self.control('play_first_video')
        if action in {'search','search_video'}:
            browser = getattr(self.registry.desktop_context, 'browser', '') or 'браузер'
            url = ('https://www.youtube.com/results?search_query=' if action == 'search_video'
                   else 'https://www.google.com/search?q=') + quote_plus(query)
            return open_url(url, self.registry.app_resolver, browser, self.registry.check_cancel, self.uia)
        before, rows, addresses = self.evidence()
        if action in {'resume_video','pause_video'}:
            # Reuse the observed user tab. A resume request must never navigate
            # to a search page or choose a new video.
            process = before.get('process','').removesuffix('.exe').casefold()
            sessions = [s for s in self.media.call('list')
                        if process in (s.get('session_id','')+' '+s.get('process','')).casefold()
                        and len(s.get('title','')) >= 3
                        and s['title'].casefold() in before.get('title','').casefold()]
            if len(sessions) != 1 or not addresses:
                return dict(verified=False, hwnd=before['hwnd'], reason='Медиа текущей вкладки не определяется однозначно')
            self.registry.check_cancel()
            if self.current()['hwnd'] != before['hwnd']:
                raise InterruptedError('Browser focus changed')
            session = sessions[0]
            playing = action == 'resume_video'
            result = self.media.call('resume' if playing else 'pause', session['session_id'])
            after, _, fresh_addresses = self.evidence()
            if (after['hwnd'] != before['hwnd'] or fresh_addresses != addresses
                    or session['title'].casefold() not in after.get('title','').casefold()):
                return dict(verified=False, hwnd=before['hwnd'], reason='Вкладка изменилась во время управления воспроизведением')
            return dict(verified=bool(result.get('verified') and result.get('playing') == playing),
                        hwnd=before['hwnd'], window=after, media_session=result, playing=result.get('playing'),
                        evidence='existing tab URL, title and browser media session')
        if action == 'play_first_video':
            if not any((urlparse(a if '://' in a else 'https://'+a).hostname or '').removeprefix('www.') == 'youtube.com' for a in addresses):
                raise LookupError('Текущая страница не подтверждена как YouTube')
            deadline=monotonic()+5
            candidates=[]
            while monotonic()<deadline:
                self.registry.check_cancel()
                before, rows, addresses=self.evidence()
                candidates = [e for e in rows if not e.get('offscreen') and e.get('rect') and
                e.get('control_type') in {'HyperlinkControl','LinkControl'} and e.get('name') and
                (re.search(r'video-title|thumbnail',e.get('automation_id',''),re.I) or
                 re.search(r'youtube.com/watch\?|/watch\?v=',str(e.get('value') or e.get('text') or '')) or
                 'ytd-video-renderer' in e.get('class_name',''))]
                if candidates: break
                sleep(.2)
            if not candidates:
                return dict(verified=False, reason='UIA не показала ссылку на видео; используй OCR/Vision по текущему снимку')
            candidates.sort(key=lambda e:(e['rect']['top'],e['rect']['left']))
            self.registry.check_cancel()
            if self.current()['hwnd'] != before['hwnd']: raise InterruptedError('Браузер сменился')
            self.click_page_element(candidates[0],before['hwnd'])
            result=self.playback(before)
            if result.get('verified'): return result
            _, rows, _ = self.evidence()
            play=[e for e in rows if e.get('control_type')=='ButtonControl' and
                  re.match(r'(?i)^(?:воспроизвести|play)(?:\s|\(|$)',e.get('name','')) and not e.get('offscreen')]
            if len(play)==1:
                self.uia.click_observed(play[0])
            return self.playback(before)
        if action == 'verify_playback': return self.playback(before)
        if action not in self.SHORTCUTS: raise ValueError('Неизвестное действие браузера')
        self.registry.check_cancel()
        press_hotkey(self.SHORTCUTS[action])
        deadline = monotonic()+3
        while monotonic()<deadline:
            self.registry.check_cancel()
            after, new_rows, new_addresses = self.evidence()
            if new_addresses != addresses or after.get('title') != before.get('title'):
                return dict(verified=True, hwnd=after['hwnd'], window=after, addresses=new_addresses, evidence='browser state changed after shortcut')
            sleep(.1)
        return dict(verified=False, hwnd=before['hwnd'], reason='Изменение вкладки не подтверждено')

    def click_page_element(self, observed, hwnd):
        root=self.uia.document_root()
        if root is None: raise LookupError('Document disappeared before click')
        matches=[(c,e) for c,e in self.uia.controls(root=root) if
                 all(e.get(k)==observed.get(k) for k in ('name','control_type','automation_id','rect'))]
        self.registry.check_cancel()
        if self.current()['hwnd']!=hwnd: raise InterruptedError('Browser focus changed')
        if len(matches)!=1: raise LookupError('Video link is no longer unambiguous')
        control,_=matches[0]
        try: control.GetInvokePattern().Invoke()
        except Exception: control.Click(simulateMove=False)


def address_matches(expected, actual):
    actual = str(actual).strip()
    if '://' not in actual: actual = 'https://' + actual
    a, b = urlparse(expected), urlparse(actual)
    return (a.hostname or '').removeprefix('www.') == (b.hostname or '').removeprefix('www.') and unquote(a.path).rstrip('/') == unquote(b.path).rstrip('/') and (not a.query or a.query == b.query)


def address_element(element):
    identity=' '.join(str(element.get(k,'')) for k in ('name','automation_id')).casefold()
    return (not element.get('password') and not element.get('offscreen')
            and element.get('control_type') in {'EditControl','Edit','ComboBoxControl','ComboBox'}
            and bool(re.search(r'urlbar|omnibox|address|адрес',identity)))


def open_url(url, resolver=None, browser='', check=lambda: None, uia=None, monitor_index=None):
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('Разрешены только абсолютные http/https URL')
    executable = None
    if resolver:
        entries = resolver.resolve(browser or 'браузер')
        executable = next((e.executable for e in entries if e.executable), None)
    from .windows import list_visible_windows, focus_window_handle, move_window_handle_to_monitor
    from .native import foreground_window
    from pathlib import Path
    from kk.vision.uia import UIAutomationService
    uia = uia or UIAutomationService()
    existing=[w for w in list_visible_windows() if executable and
              str(Path(w.get('executable',''))).casefold()==str(Path(executable)).casefold()]
    target_hwnd=None
    check()
    if existing:
        from .keyboard import press_hotkey, type_text
        active=foreground_window().get('hwnd')
        target=next((w for w in existing if w['hwnd']==active),existing[0])
        target_hwnd=target['hwnd']
        focus_window_handle(target_hwnd)
        if foreground_window().get('hwnd') != target_hwnd:
            raise InterruptedError('Browser focus was not acquired')
        press_hotkey(['ctrl','t']); press_hotkey(['ctrl','l'])
        sleep(.15)
        check()
        _,address=uia.focused()
        if foreground_window().get('hwnd')!=target_hwnd or not address_element(address):
            return dict(opened=False,verified=False,error='New tab address field not focused')
        type_text(url,cancellation_check=check)
        _,address=uia.focused()
        if foreground_window().get('hwnd')!=target_hwnd or not address_matches(url,address.get('value') or address.get('text') or ''):
            return dict(opened=False,verified=False,error='Address text not verified before Enter')
        check();press_hotkey(['enter'])
        opened=True
    elif executable:
        subprocess.Popen([executable, url], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        opened = True
    elif browser:
        raise LookupError('Не найден исполняемый файл браузера ' + browser)
    else:
        opened = webbrowser.open(url, new=2)
    deadline = monotonic() + 8
    inspected_address=set()
    while opened and monotonic() < deadline:
        check()
        windows = list_visible_windows()
        matches = [w for w in windows if (Path(w.get('executable','')).name.casefold() == Path(executable).name.casefold() if executable else w.get('process','').casefold() in {'firefox.exe','chrome.exe','msedge.exe','brave.exe'})]
        for window in matches:
            if target_hwnd and window['hwnd']!=target_hwnd: continue
            if foreground_window().get('hwnd') != window['hwnd']:
                if target_hwnd:
                    return dict(opened=opened, verified=False, hwnd=target_hwnd, error='Browser lost focus; navigation verification stopped')
                focus_window_handle(window['hwnd'])
            elements = uia.tree(interactive_only=False)
            addresses = [e.get('value') or e.get('text') for e in elements
                         if address_element(e)]
            if not any(addresses) and window['hwnd'] not in inspected_address:
                from .keyboard import press_hotkey
                check()
                if foreground_window().get('hwnd') != window['hwnd']: continue
                press_hotkey(['ctrl','l'])
                sleep(.1)
                _, address=uia.focused()
                if address_element(address): addresses.append(address.get('value') or address.get('text'))
                press_hotkey(['escape'])
                inspected_address.add(window['hwnd'])
            if foreground_window().get('hwnd') != window['hwnd']:
                continue
            if any(address_matches(url, value) for value in addresses if value):
                if monitor_index and window.get('monitor_index') != monitor_index:
                    window = move_window_handle_to_monitor(window['hwnd'], monitor_index)['after']
                return dict(opened=True, verified=True, url=url, browser=executable, hwnd=window['hwnd'], monitor_index=window.get('monitor_index'), process_id=window.get('process_id',0), window=window, evidence='UIA address matches requested URL')
        sleep(.15)
    return dict(opened=opened, verified=False, url=url, browser=executable, error='Адрес страницы не подтверждён через UIA')
