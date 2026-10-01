"""Deterministic local resources + upstream wheel license texts for the client."""
from pathlib import Path
import importlib.metadata
import json
import shutil
import sys
import subprocess
import hashlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

root=Path(__file__).resolve().parents[1]
(root/'build').mkdir(exist_ok=True)
(root/'onyx_desktop'/'backend.json').write_text(json.dumps({'root':str(root)}),encoding='utf-8')
# Canonical prepared PNG is tracked; packaging must never resample it in-place.
app=QGuiApplication([])
image=QImage(str(root/'onyx_desktop/assets/pearl.png')).scaled(256,256,Qt.KeepAspectRatio,Qt.SmoothTransformation)
if not image.save(str(root/'build/onyx.ico')):raise RuntimeError('Could not write Windows icon')
import PySide6
qsb=Path(PySide6.__file__).parent/'qsb.exe'
shader_dir=root/'onyx_desktop/assets'
manifest=json.loads((shader_dir/'shader-manifest.json').read_text(encoding='utf-8'))
if PySide6.__version__!=manifest['qt_version']:raise RuntimeError('Shader Qt version differs from pinned build')
if qsb.is_file():
    subprocess.run([str(qsb),'--glsl','100 es,120,150','--hlsl','50','--msl','12','-o',
        str(shader_dir/'title.frag.qsb'),str(shader_dir/'title.frag')],check=True)
# Essentials intentionally excludes ShaderTools. The tracked precompiled shader
# is accepted only with its exact source/binary fingerprints and pinned Qt.
for filename,digest in manifest['sha256'].items():
    if hashlib.sha256((shader_dir/filename).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Stale shader: recompile with Qt 6.11.2 qsb and update shader-manifest.json')
notices=root/'build/desktop-notices';notices.mkdir(exist_ok=True)
for package in ('PySide6_Essentials','shiboken6','psutil','Nuitka'):
    dist=importlib.metadata.distribution(package)
    for file in dist.files or []:
        if any(word in str(file).lower() for word in ('license','copying','copyright')) and dist.locate_file(file).is_file():
            target=notices/package/str(file)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(dist.locate_file(file),target)
python_license=Path(sys.base_prefix)/'LICENSE.txt'
if python_license.is_file():shutil.copy2(python_license,notices/'PYTHON-LICENSE.txt')
