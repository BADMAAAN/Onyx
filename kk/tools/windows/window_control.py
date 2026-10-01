"""Native, HWND-addressed window operations with bounded read-back verification."""
import ctypes as c
from ctypes import wintypes as w
from time import monotonic, sleep
from .native import foreground_window, window_metadata
from .windows import list_visible_windows, focus_window_handle

u = c.WinDLL('user32', use_last_error=True)
for name, result, args in [('IsWindow',w.BOOL,[w.HWND]), ('IsZoomed',w.BOOL,[w.HWND]),
    ('IsIconic',w.BOOL,[w.HWND]), ('ShowWindowAsync',w.BOOL,[w.HWND,c.c_int]),
    ('PostMessageW',w.BOOL,[w.HWND,w.UINT,w.WPARAM,w.LPARAM]),
    ('SetWindowPos',w.BOOL,[w.HWND,w.HWND,c.c_int,c.c_int,c.c_int,c.c_int,w.UINT])]:
    fn=getattr(u,name); fn.restype=result; fn.argtypes=args


class WindowController:
    def __init__(self, registry): self.registry=registry

    def target(self, hwnd=None):
        context=getattr(self.registry,'desktop_context',None)
        if not hwnd:
            if context and context.recent() and context.hwnd:
                hwnd=context.hwnd
            else: hwnd=foreground_window().get('hwnd')
        if not hwnd or not u.IsWindow(hwnd): raise LookupError('Target window disappeared')
        window=window_metadata(hwnd)
        from kk.ui.capture_guard import is_own_window
        if is_own_window(hwnd): raise ValueError('Select a desktop application')
        # A deterministic plan passes the remembered handle explicitly; that
        # must not bypass identity validation when Windows recycles an HWND.
        if context and context.hwnd == hwnd and getattr(context,'process_id',0) and window.get('process_id') != context.process_id:
            raise LookupError('Referenced window was replaced; select an application again')
        if window.get('title') in {'KK Orb','KK Edge Presence','Onyx Orb','Onyx Edge Presence'}: raise ValueError('Select a desktop application')
        return window

    def state(self, hwnd=None):
        target=self.target(hwnd)
        return dict(target,maximized=bool(u.IsZoomed(target['hwnd'])),minimized=bool(u.IsIconic(target['hwnd'])))

    def remember(self, window):
        from kk.core.desktop_commands import DesktopContext
        context = getattr(self.registry, 'desktop_context', None) or DesktopContext()
        if context.hwnd != window['hwnd']:
            context.app = window.get('process') or window['title']
        context.hwnd, context.updated, context.process_id = window['hwnd'], monotonic(), window.get('process_id',0)
        context.last_monitor, context.last_window = window.get('monitor_index'), window
        self.registry.desktop_context = context

    def change(self, operation, hwnd=None, **bounds):
        before=self.state(hwnd); hwnd=before['hwnd']; self.registry.check_cancel()
        if operation=='close': u.PostMessageW(hwnd,0x0010,0,0)
        elif operation=='bounds':
            if bounds['width']<80 or bounds['height']<60: raise ValueError('Window size too small')
            if before['maximized'] or before['minimized']:
                u.ShowWindowAsync(hwnd,9)
                deadline=monotonic()+1
                while u.IsZoomed(hwnd) or u.IsIconic(hwnd):
                    self.registry.check_cancel()
                    if monotonic()>deadline: raise RuntimeError('Window did not restore before setting bounds')
                    sleep(.03)
            if not u.SetWindowPos(hwnd,None,bounds['x'],bounds['y'],bounds['width'],bounds['height'],0x0014): raise c.WinError(c.get_last_error())
        elif operation=='focus': focus_window_handle(hwnd)
        else: u.ShowWindowAsync(hwnd, {'maximize':3,'restore':9,'minimize':6}[operation])
        deadline=monotonic()+3
        after=None
        while monotonic()<deadline:
            self.registry.check_cancel()
            if operation=='close' and not u.IsWindow(hwnd): return dict(verified=True,closed=True,before=before,hwnd=hwnd)
            after=self.state(hwnd)
            valid = (operation=='maximize' and after['maximized'] or operation=='minimize' and after['minimized']
                or operation=='restore' and not after['maximized'] and not after['minimized']
                or operation=='focus' and foreground_window()['hwnd']==hwnd
                or operation=='bounds' and all(abs(a-b)<=2 for a,b in zip(after['bounds'],
                    [bounds['x'],bounds['y'],bounds['x']+bounds['width'],bounds['y']+bounds['height']])))
            if valid:
                self.remember(after)
                return dict(verified=True,before=before,after=after,hwnd=hwnd)
            sleep(.03)
        if operation=='close': return dict(verified=False,close_requested=True,after=after,note='Window remains open; inspect save dialog')
        raise RuntimeError(operation+' did not reach the requested window state')

    def fullscreen(self, hwnd=None):
        window=self.target(hwnd)
        if window.get('process','').casefold() not in {'firefox.exe','chrome.exe','msedge.exe','brave.exe','vlc.exe'}:
            raise ValueError('No verified fullscreen shortcut for this app; use maximize_window or inspect its UI')
        from kk.ui.orb_model import is_fullscreen
        from kk.ui.orb_window import foreground_info
        from .native import monitor_for_window
        from .keyboard import press_hotkey
        focus_window_handle(window['hwnd'])
        before=is_fullscreen(foreground_info(),monitor_for_window(window['hwnd']))
        self.registry.check_cancel(); press_hotkey(['f11'] if window['process'].casefold()!='vlc.exe' else ['f'])
        deadline=monotonic()+3
        while monotonic()<deadline:
            self.registry.check_cancel()
            if foreground_window()['hwnd']!=window['hwnd']: raise RuntimeError('Focus changed during fullscreen verification')
            after=is_fullscreen(foreground_info(),monitor_for_window(window['hwnd']))
            if after!=before: return dict(verified=True,fullscreen=after,hwnd=window['hwnd'])
            sleep(.05)
        raise RuntimeError('Fullscreen change not confirmed')
