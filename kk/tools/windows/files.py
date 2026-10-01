from __future__ import annotations

from pathlib import Path
from typing import Any


MAX_TEXT_BYTES = 1_000_000


def downloads_folder() -> Path:
    """Windows' redirected Downloads known folder, not a guessed home path."""
    from .shell_metadata import known_folder
    return known_folder('downloads')


def list_downloads():
    target = downloads_folder()
    snapshot = directory_observation(str(target))
    entries = snapshot['items']
    # Filesystem mtime is not evidence of when a download occurred.
    return dict(snapshot, path=str(target), entries=entries, metadata_source='filesystem',
            date_meaning='modified_at, not download time', contents_read=False)


def directory_observation(path):
    from .shell_metadata import directory_snapshot
    snapshot = directory_snapshot(path)
    # Retain the historic directory label alongside typed resolver metadata.
    for item in snapshot['items']:
        item['type'] = 'directory' if item['type'] == 'folder' else item['type']
    return snapshot


def list_directory(path: str) -> list[dict[str, Any]]:
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        raise NotADirectoryError(target)
    return [
        {"name": item.name, "type": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else None}
        for item in sorted(target.iterdir(), key=lambda value: (not value.is_dir(), value.name.casefold()))[:500]
    ]


def read_text_file(path: str) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    forbidden = {'.ssh', '.aws', '.azure', 'credentials', 'credentials.json', 'auth.json', 'tokens.json',
                 'cookies', 'login data', 'key4.db', 'logins.json', 'wallet.dat'}
    if (set(p.casefold() for p in target.parts) & forbidden or target.suffix.casefold() in {'.pem', '.key', '.pfx'}
            or target.name.casefold().startswith(('.env', 'id_rsa', 'id_ed25519'))):
        raise PermissionError('Credential and private-key stores are excluded from agent access')
    if not target.is_file():
        raise FileNotFoundError(target)
    if target.stat().st_size > MAX_TEXT_BYTES:
        raise ValueError(f"Файл больше лимита {MAX_TEXT_BYTES} байт")
    return {"path": str(target), "content": target.read_text(encoding="utf-8", errors="replace")}
