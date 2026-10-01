"""Fail closed before distributing a standalone client; no developer PATH needed."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

REQUIRED = (
    'Onyx.exe', 'python312.dll', 'python3.dll', 'vcruntime140.dll',
    'qt6core.dll', 'qt6gui.dll', 'qt6quick.dll', 'qt6qml.dll', 'qt6svg.dll',
    'PySide6/qt-plugins/platforms/qwindows.dll',
    'onyx_desktop/qml/Main.qml', 'onyx_desktop/qml/IconButton.qml',
    'onyx_desktop/assets/pearl.png', 'onyx_desktop/assets/icons/mic.svg',
    'onyx_desktop/assets/onyx-original.png', 'onyx_desktop/assets/qwen.png',
    'onyx_desktop/assets/openai.png', 'onyx_desktop/qml/ModelsPanel.qml',
    'onyx_desktop/qml/AppsPage.qml', 'onyx_desktop/qml/ChatSidebar.qml',
    'onyx_desktop/assets/hermes.png', 'onyx_desktop/assets/cline.png',
    'onyx_desktop/assets/oh-my-pi.png', 'onyx_desktop/assets/deepseek-harness.png',
    'onyx_desktop/qml/GitPanel.qml', 'onyx_desktop/qml/ModelPicker.qml',
    'onyx_desktop/qml/InlineLive.qml', 'onyx_desktop/assets/openclaw.svg',
    'onyx_desktop/assets/pi.svg', 'onyx_desktop/assets/copilot.svg',
    'onyx_desktop/assets/huggingface.svg', 'onyx_desktop/assets/app-sources.json',
    'onyx_desktop/assets/factory.svg', 'onyx_desktop/assets/factory-qt.svg',
    'onyx_desktop/assets/deepseek.png',
    'onyx_desktop/assets/meta-llama.png', 'onyx_desktop/assets/NOTICE-PUBLISHER-MARKS',
    'onyx_desktop/NOTICES.md', 'notices/PYTHON-LICENSE.txt',
)

def audit(folder):
    folder = Path(folder)
    for name in REQUIRED:
        path = folder / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Incomplete standalone bundle: ' + name)
    for name in ('Onyx.exe', 'python312.dll', 'qt6core.dll'):
        data = (folder / name).read_bytes()
        offset = struct.unpack_from('<I', data, 0x3c)[0]
        if data[:2] != b'MZ' or data[offset:offset+4] != b'PE\0\0' or struct.unpack_from('<H', data, offset+4)[0] != 0x8664:
            raise ValueError('Expected x64 PE: ' + name)
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*')) if p.is_file() and p.name != 'manifest.json'}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder', type=Path)
    p.add_argument('--build-id')
    p.add_argument('--revision')
    args = p.parse_args()
    files = audit(args.folder)
    manifest = args.folder / 'manifest.json'
    if args.build_id:
        manifest.write_text(json.dumps(dict(version='0.5.0', build_id=args.build_id,
            revision=args.revision, architecture='x64', backend_required=True,
            files=files), indent=2), encoding='utf-8')
    elif not manifest.is_file() or json.loads(manifest.read_text(encoding='utf-8'))['files'] != files:
        raise ValueError('Bundle manifest mismatch')
    print('Verified standalone files:', len(files))

if __name__ == '__main__': main()
