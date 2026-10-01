from __future__ import annotations

import ctypes
from typing import Any


VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002


def get_volume():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    try:
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        return {'percent': round(endpoint.GetMasterVolumeLevelScalar()*100), 'muted': bool(endpoint.GetMute())}
    finally:
        comtypes.CoUninitialize()


def _tap(key: int) -> None:
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


def set_volume(level: int) -> dict[str, Any]:
    if not 0 <= level <= 100:
        raise ValueError("Громкость должна быть от 0 до 100")
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    try:
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        endpoint.SetMasterVolumeLevelScalar(level/100, None)
        return {'requested_level':level,'actual_level':round(endpoint.GetMasterVolumeLevelScalar()*100),
                'method':'Windows Core Audio','muted':bool(endpoint.GetMute())}
    finally:
        comtypes.CoUninitialize()
