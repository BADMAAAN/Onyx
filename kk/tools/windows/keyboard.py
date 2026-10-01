from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any
from time import sleep


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]


KEYS = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12,
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
}
KEYS.update({f'f{i}':0x6F+i for i in range(1,25)})


def _send(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
    item = INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))
    if ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
        raise OSError("SendInput failed")


def type_text(text: str, cancellation_check=None) -> dict[str, Any]:
    if len(text) > 10_000:
        raise ValueError("Текст превышает лимит 10 000 символов")
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    encoded = text.encode('utf-16-le')
    for index in range(0,len(encoded),2):
        if cancellation_check:
            cancellation_check()
        if ctypes.windll.user32.GetForegroundWindow() != hwnd:
            raise InterruptedError('Foreground changed during typing')
        code = int.from_bytes(encoded[index:index+2], 'little')
        _send(scan=code, flags=KEYEVENTF_UNICODE)
        _send(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        sleep(.012)
    return {"typed_characters": len(text)}


def press_hotkey(keys: list[str], allow_windows=False) -> dict[str, Any]:
    if not 1 <= len(keys) <= 4:
        raise ValueError("Комбинация должна содержать от 1 до 4 клавиш")
    codes = []
    for key in keys:
        normalized = key.casefold()
        if normalized in {"win", "windows", "meta", "super"} and not allow_windows:
            raise ValueError("Клавиша Windows запрещена")
        code = 0x5B if allow_windows and normalized in {"win", "windows", "meta", "super"} else KEYS.get(normalized)
        if code is None and len(key) == 1:
            # Shortcut letters are virtual keys and must not depend on the
            # currently selected RU/EN keyboard layout.
            code = ord(key.upper()) if key.isascii() and key.isalnum() else ctypes.windll.user32.VkKeyScanW(key) & 0xFF
        if code is None or code == 0xFF:
            raise ValueError(f"Неизвестная клавиша: {key}")
        codes.append(code)
    for code in codes:
        _send(vk=code)
    for code in reversed(codes):
        _send(vk=code, flags=KEYEVENTF_KEYUP)
    return {"pressed": keys}
