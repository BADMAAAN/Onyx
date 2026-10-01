"""Consistent local backups: SQLite online backup, never copy an open DB file."""
from pathlib import Path
from datetime import datetime, timezone
import json
import shutil
import sqlite3


def backup(root):
    root = Path(root).resolve()
    destination = root / 'data' / 'backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    destination.mkdir(parents=True)
    records = []
    for source in sorted((root / 'data').glob('*.db')):
        target = destination / source.name
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=15) as live:
            with sqlite3.connect(target) as saved:
                live.backup(saved, pages=256, sleep=.05)
                integrity = saved.execute('PRAGMA integrity_check').fetchone()[0]
                if integrity != 'ok':
                    raise RuntimeError(f'Backup verification failed: {source.name}: {integrity}')
        records.append({'file': source.name, 'source':str(source.resolve()),'backup':str(target.resolve()),'method': 'sqlite3.Connection.backup', 'integrity_check': integrity})
    for relative in ('config.toml', 'data/preferences.json', 'data/personal_intents.json', 'data/app_corrections.json'):
        source = root / relative
        if source.is_file():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    manifest = {'root':str(root),'created_utc': datetime.now(timezone.utc).isoformat(), 'databases': records,
                'retention': 'Local backups retained until explicitly removed; Forget does not erase past backups.'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return destination


if __name__ == '__main__':
    print(backup(Path(__file__).resolve().parents[1]))
