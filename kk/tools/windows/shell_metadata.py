"""Bounded Windows Shell metadata. No document contents or recursive disk scan."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import uuid

from kk.tools.observation import observation


KNOWN_FOLDERS = {
    'desktop': 'B4BFCC3A-DB2C-424C-B029-7FE99A87C641',
    'public_desktop': 'C4AA340D-F20F-4863-AFEF-F87EF2E6BA25',
    'downloads': '374DE290-123F-4565-9164-39C4925E467B',
}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def known_folder(name):
    """SHGetKnownFolderPath honors folder redirection, including OneDrive."""
    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                    ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]
    guid = GUID.from_buffer_copy(uuid.UUID(KNOWN_FOLDERS[name]).bytes_le)
    address = ctypes.c_void_p()
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    fn = shell.SHGetKnownFolderPath
    fn.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    fn.restype = ctypes.c_long
    result = fn(ctypes.byref(guid), 0, None, ctypes.byref(address))
    if result != 0:
        raise OSError(f'SHGetKnownFolderPath({name}) failed: {result:#x}')
    try:
        return Path(ctypes.wstring_at(address))
    finally:
        free = ctypes.windll.ole32.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free(address)


def shell_snapshot(mode):
    if mode not in {'desktop', 'explorer'}:
        raise ValueError('Unsupported Shell metadata scope')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
        str(Path(__file__).with_name('shell_metadata.ps1')), '-Mode', mode],
        capture_output=True, encoding='utf-8', errors='replace', timeout=15,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
    data = json.loads(result.stdout.lstrip('\ufeff') or '{}')
    return observation(data.get('items', []), source='Windows Shell.Application',
        scope=mode, completeness=data.get('completeness', 'partial'),
        limitations=data.get('limitations', []))


def directory_snapshot(path, source='filesystem', limit=500):
    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ValueError('An absolute folder path is required')
    items, limitations = [], []
    checked = timestamp()
    with os.scandir(target) as children:
        for child in children:
            if len(items) >= limit:
                limitations.append(f'Metadata limit reached ({limit} direct children)')
                break
            try:
                # Do not traverse junctions or symlinks. Their target is inspected
                # only when an explicitly selected item is revalidated for action.
                folder = child.is_dir(follow_symlinks=True)
                stat = child.stat(follow_symlinks=True)
                items.append(dict(name=child.name, type='folder' if folder else 'file',
                    path=child.path, source=source, verified_at=checked,
                    size=None if folder else stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    is_link=child.is_symlink(), is_shortcut=child.name.casefold().endswith('.lnk')))
            except OSError as exc:
                limitations.append(f'Unavailable item {child.name}: {exc}')
    return observation(items, source=source, scope=str(target),
        completeness='partial' if limitations else 'complete', limitations=limitations)


def explorer_locations():
    """Shell.Windows exposes locations, but does not promise all Win11 tabs."""
    try:
        return shell_snapshot('explorer')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return observation([], source='Windows Shell.Application', scope='explorer',
            completeness='unknown', limitations=[str(exc)])


def path_identity(path):
    return os.path.normcase(os.path.realpath(path))


def open_folder_verified(path, cancellation_check=None):
    """Reuse an observed Explorer location; verify the path after launch/focus."""
    from time import monotonic, sleep
    from .windows import focus_window_handle
    target = Path(path)
    if not target.is_dir():
        raise NotADirectoryError(target)
    wanted = path_identity(target)
    before = explorer_locations()
    existing = [item for item in before['items'] if item.get('path') and
                path_identity(item['path']) == wanted]
    if cancellation_check:
        cancellation_check()
    if existing:
        selected = existing[0]
        focus_window_handle(selected['hwnd'])
    else:
        os.startfile(str(target))
    deadline = monotonic() + 5
    latest = before
    while monotonic() < deadline:
        if cancellation_check:
            cancellation_check()
        latest = explorer_locations()
        found = [item for item in latest['items'] if item.get('path') and
                 path_identity(item['path']) == wanted]
        if found:
            selected = found[0]
            focused = focus_window_handle(selected['hwnd'])
            return dict(verified=True, path=str(target), hwnd=selected['hwnd'],
                reused=bool(existing), location=selected, coverage=latest, window=focused,
                process_id=focused.get('process_id'), executable=focused.get('executable'),
                monitor_index=focused.get('monitor_index'))
        sleep(.15)
    return dict(verified=False, path=str(target), opened=True, coverage=latest,
        reason='Открытие запрошено, но расположение в Проводнике не подтверждено')
