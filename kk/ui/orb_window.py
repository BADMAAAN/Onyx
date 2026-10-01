"""Small Win32 layered window. All handles and callbacks live on its owner thread."""
import ctypes as c
from ctypes import wintypes as w
import logging
import uuid
import numpy as np

u = c.WinDLL('user32', use_last_error=True)
g = c.WinDLL('gdi32', use_last_error=True)
k = c.WinDLL('kernel32', use_last_error=True)
LRESULT = c.c_ssize_t
WNDPROC = c.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASS(c.Structure):
    _fields_ = [('style', w.UINT), ('proc', WNDPROC), ('cls_extra', c.c_int),
                ('wnd_extra', c.c_int), ('instance', w.HINSTANCE), ('icon', w.HICON),
                ('cursor', w.HANDLE), ('background', w.HBRUSH),
                ('menu', w.LPCWSTR), ('name', w.LPCWSTR)]


class BITMAPINFO(c.Structure):
    _fields_ = [('size', w.DWORD), ('width', w.LONG), ('height', w.LONG),
                ('planes', w.WORD), ('bits', w.WORD), ('compression', w.DWORD),
                ('image_size', w.DWORD), ('xppm', w.LONG), ('yppm', w.LONG),
                ('used', w.DWORD), ('important', w.DWORD), ('color', w.DWORD)]


class BLEND(c.Structure):
    _fields_ = [('operation', w.BYTE), ('flags', w.BYTE), ('alpha', w.BYTE), ('format', w.BYTE)]


def signature(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


signature(k, 'GetModuleHandleW', w.HMODULE, w.LPCWSTR)
signature(u, 'RegisterClassW', w.ATOM, c.POINTER(WNDCLASS))
signature(u, 'UnregisterClassW', w.BOOL, w.LPCWSTR, w.HINSTANCE)
signature(u, 'CreateWindowExW', w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
          c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p)
signature(u, 'DefWindowProcW', LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
signature(u, 'DestroyWindow', w.BOOL, w.HWND)
signature(u, 'ShowWindow', w.BOOL, w.HWND, c.c_int)
signature(u, 'SetWindowPos', w.BOOL, w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
signature(u, 'GetWindowRect', w.BOOL, w.HWND, c.POINTER(w.RECT))
signature(u, 'GetCursorPos', w.BOOL, c.POINTER(w.POINT))
signature(u, 'SetCapture', w.HWND, w.HWND)
signature(u, 'ReleaseCapture', w.BOOL)
signature(u, 'GetForegroundWindow', w.HWND)
signature(u, 'GetClassNameW', c.c_int, w.HWND, w.LPWSTR, c.c_int)
signature(u, 'GetWindowLongW', w.LONG, w.HWND, c.c_int)
signature(u, 'GetAsyncKeyState', c.c_short, c.c_int)
signature(u, 'IsIconic', w.BOOL, w.HWND)
signature(u, 'IsWindowVisible', w.BOOL, w.HWND)
signature(u, 'GetDC', w.HDC, w.HWND)
signature(u, 'ReleaseDC', c.c_int, w.HWND, w.HDC)
signature(u, 'PeekMessageW', w.BOOL, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
signature(u, 'TranslateMessage', w.BOOL, c.POINTER(w.MSG))
signature(u, 'DispatchMessageW', LRESULT, c.POINTER(w.MSG))
signature(u, 'UpdateLayeredWindow', w.BOOL, w.HWND, w.HDC, c.POINTER(w.POINT),
          c.POINTER(w.SIZE), w.HDC, c.POINTER(w.POINT), w.DWORD, c.POINTER(BLEND), w.DWORD)
signature(g, 'CreateCompatibleDC', w.HDC, w.HDC)
signature(g, 'CreateDIBSection', w.HBITMAP, w.HDC, c.POINTER(BITMAPINFO), w.UINT,
          c.POINTER(c.c_void_p), w.HANDLE, w.DWORD)
signature(g, 'SelectObject', w.HANDLE, w.HDC, w.HANDLE)
signature(g, 'DeleteObject', w.BOOL, w.HANDLE)
signature(g, 'DeleteDC', w.BOOL, w.HDC)


def foreground_info(hwnd=None):
    hwnd = hwnd or u.GetForegroundWindow()
    rect = w.RECT()
    if not hwnd or not u.GetWindowRect(hwnd, c.byref(rect)):
        return {}
    name = c.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, name, 256)
    return dict(hwnd=hwnd, bounds=[rect.left, rect.top, rect.right, rect.bottom],
                center=((rect.left+rect.right)//2, (rect.top+rect.bottom)//2),
                minimized=bool(u.IsIconic(hwnd)), class_name=name.value,
                has_caption=bool(u.GetWindowLongW(hwnd, -16) & 0x00C00000))


class OrbWindow:
    def __init__(self, size, mouse, hit):
        self.size, self.mouse, self.hit = size, mouse, hit
        self.hwnd = self.dc = self.bitmap = self.old_bitmap = None
        self.previous_dpi = None
        if hasattr(u, 'SetThreadDpiAwarenessContext'):
            signature(u, 'SetThreadDpiAwarenessContext', c.c_void_p, c.c_void_p)
            self.previous_dpi = u.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        self.name = 'KKOrb_' + uuid.uuid4().hex
        self.instance = k.GetModuleHandleW(None)
        self.callback = WNDPROC(self._dispatch)
        self.wc = WNDCLASS(0, self.callback, 0, 0, self.instance, None, None, None, None, self.name)
        if not u.RegisterClassW(c.byref(self.wc)):
            raise c.WinError(c.get_last_error())
        try:
            # NOACTIVATE | TOOLWINDOW | LAYERED. No WS_EX_TRANSPARENT: the disc
            # accepts drag; zero-alpha pixels pass clicks to windows underneath.
            self.hwnd = u.CreateWindowExW(0x08080080, self.name, 'Onyx Orb', 0x80000000,
                0, 0, size, size, None, None, self.instance, None)
            if not self.hwnd:
                raise c.WinError(c.get_last_error())
            screen = u.GetDC(None)
            try:
                self.dc = g.CreateCompatibleDC(screen)
                info = BITMAPINFO(40, size, -size, 1, 32, 0, size*size*4, 0, 0, 0, 0, 0)
                self.bits = c.c_void_p()
                self.bitmap = g.CreateDIBSection(screen, c.byref(info), 0, c.byref(self.bits), None, 0)
            finally:
                u.ReleaseDC(None, screen)
            if not self.dc or not self.bitmap:
                raise c.WinError(c.get_last_error())
            self.old_bitmap = g.SelectObject(self.dc, self.bitmap)
        except Exception:
            self.close()
            raise

    @staticmethod
    def cursor():
        point = w.POINT()
        u.GetCursorPos(c.byref(point))
        return point.x, point.y

    def _dispatch(self, hwnd, message, wp, lp):
        try:
            if message == 0x0021:  # WM_MOUSEACTIVATE
                return 3  # MA_NOACTIVATE (deliver click, preserve foreground)
            if message == 0x0084:  # WM_NCHITTEST: signed virtual desktop coordinates
                x, y = c.c_short(lp & 0xffff).value, c.c_short((lp >> 16) & 0xffff).value
                rect = w.RECT()
                u.GetWindowRect(hwnd, c.byref(rect))
                return 1 if self.hit(x-rect.left, y-rect.top) else -1
            if message == 0x0201:
                u.SetCapture(hwnd)
                self.mouse('down', self.cursor())
                return 0
            if message == 0x0200:
                self.mouse('move', self.cursor())
                return 0
            if message == 0x0202:
                self.mouse('up', self.cursor())
                u.ReleaseCapture()
                return 0
            if message == 0x0205:
                self.mouse('right', self.cursor())
                return 0
            if message in (0x0215, 0x001F):
                self.mouse('cancel', self.cursor())
                return 0
        except Exception:
            logging.getLogger(__name__).exception('Orb window event failed')
        return u.DefWindowProcW(hwnd, message, wp, lp)

    def present(self, image, x, y):
        rgba = np.asarray(image, dtype=np.uint16)
        bgra = rgba[..., [2, 1, 0, 3]].copy()
        bgra[..., :3] = (bgra[..., :3]*bgra[..., 3:4]+127)//255
        data = bgra.astype(np.uint8).tobytes()
        c.memmove(self.bits, data, len(data))
        destination, source = w.POINT(x, y), w.POINT(0, 0)
        size, blend = w.SIZE(self.size, self.size), BLEND(0, 0, 255, 1)
        if not u.UpdateLayeredWindow(self.hwnd, None, c.byref(destination), c.byref(size),
                                     self.dc, c.byref(source), 0, c.byref(blend), 2):
            raise c.WinError(c.get_last_error())

    def move(self, x, y):
        u.SetWindowPos(self.hwnd, -1, x, y, 0, 0, 0x0011)  # NOACTIVATE | NOSIZE

    @staticmethod
    def left_button_down():
        return bool(u.GetAsyncKeyState(1) & 0x8000)

    def show(self, visible):
        if visible:
            u.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x0053)
        else:
            u.ShowWindow(self.hwnd, 0)

    @staticmethod
    def release_capture():
        u.ReleaseCapture()

    def pump(self):
        message = w.MSG()
        while u.PeekMessageW(c.byref(message), None, 0, 0, 1):
            if message.message == 0x0012:
                return False
            u.TranslateMessage(c.byref(message))
            u.DispatchMessageW(c.byref(message))
        return True

    def close(self):
        if self.hwnd:
            u.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.old_bitmap:
            g.SelectObject(self.dc, self.old_bitmap)
        if self.bitmap:
            g.DeleteObject(self.bitmap)
        if self.dc:
            g.DeleteDC(self.dc)
        u.UnregisterClassW(self.name, self.instance)
        if self.previous_dpi:
            u.SetThreadDpiAwarenessContext(self.previous_dpi)
