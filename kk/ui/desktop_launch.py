"""Shared tray and voice return target; never resolve an arbitrary same-name app."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

def installed_client():
    root=Path(os.environ['USERPROFILE'])/'Onyx/Client'
    try:
        pointer=json.loads((root/'current.json').read_text(encoding='utf-8-sig'))
        exe=Path(pointer['executable']).resolve()
        if not exe.is_relative_to(root.resolve()) or exe.name!='Onyx.exe':return None
        manifest=json.loads((exe.parent/'manifest.json').read_text(encoding='utf-8'))
        if hashlib.sha256(exe.read_bytes()).hexdigest()!=manifest['files']['Onyx.exe']:return None
        return exe
    except (OSError,ValueError,KeyError):return None

def show(root):
    root=Path(root)
    bundle=installed_client() or root/'dist/Onyx.dist/Onyx.exe'
    command=[str(bundle)] if bundle.is_file() else [str(Path(sys.executable).with_name('pythonw.exe')),'-m','onyx_desktop']
    subprocess.Popen([*command,'--backend-root',str(root)],cwd=root,creationflags=subprocess.CREATE_NO_WINDOW)
    return 'Показ окна Onyx запрошен.'
