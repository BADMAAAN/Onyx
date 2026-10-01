"""Immutable startup identity; status never hashes changed files as loaded code."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def source_manifest(root):
    root = Path(root).resolve()
    paths = set()
    for folder in ('app', 'kk'):
        for pattern in ('*.py', '*.json', '*.qml', '*.frag', '*.qsb', '*.ps1'):
            paths.update((root / folder).rglob(pattern))
    paths.update(root / name for name in ('run.ps1', 'pyproject.toml', 'requirements-lock.txt'))
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(paths) if p.is_file() and '__pycache__' not in p.parts}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(source_sha256=digest, source_files=files)


def startup_identity(root, config_path):
    root = Path(root).resolve()
    try:
        revision = subprocess.check_output(
            ['git', '-c', 'safe.directory=' + root.as_posix(), 'rev-parse', 'HEAD'],
            cwd=root, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
    except (OSError, subprocess.SubprocessError):
        revision = 'unknown'
    manifest = source_manifest(root)
    return dict(commit=revision, source_sha256=manifest['source_sha256'],
                source_file_count=len(manifest['source_files']),
                config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
                config=str(Path(config_path).resolve()), root=str(root), executable=sys.executable,
                python=sys.version, prefix=sys.prefix, base_prefix=sys.base_prefix,
                started_utc=datetime.now(timezone.utc).isoformat())
