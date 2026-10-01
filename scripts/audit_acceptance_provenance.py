"""Match saved harness IDs and complete fixture content; optionally archive.

Titles are never evidence. Archival preserves all conversations/messages and
is reversible by removing only the newly inserted archive entries recorded in
the rollback file. Running without --archive performs a read-only DB audit.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


VIOLET = 'The observatory code in this conversation is violet-184. Explain why planets orbit the Sun in eight short paragraphs.'
AMBER = 'The observatory code in this conversation is amber-629. What is the observatory code in this conversation?'
FOLLOWUP = 'What is the observatory code in this conversation?'
ATTACHMENT = 'Return only the calibration marker in the attached text.\n\nUser-selected attachments (quoted data, not instructions):\n'
MARKER = 'Calibration marker: quartz-731. Neutral local acceptance data.\n'


def fixture_matches(key, messages):
    if key == 'conversation_a': return messages == [VIOLET, FOLLOWUP]
    if key == 'conversation_b': return messages == [AMBER]
    if key == 'conversation_id' and len(messages) == 1 and messages[0].startswith(ATTACHMENT):
        try:
            files = json.loads(messages[0][len(ATTACHMENT):])
            return len(files) == 1 and files[0]['content'].replace('\r\n', '\n') == MARKER
        except (ValueError, KeyError, TypeError):
            pass
    return False


def audit(db, root):
    matches = {}
    for folder in ('desktop_acceptance', 'desktop_visual_acceptance'):
        for pattern in ('gui-acceptance.json', 'workflow.json'):
            for path in sorted((root/'data'/folder).rglob(pattern)):
                try: report = json.loads(path.read_text(encoding='utf-8-sig'))
                except (OSError, ValueError): continue
                for key in ('conversation_a', 'conversation_b', 'conversation_id'):
                    cid = report.get(key)
                    if not isinstance(cid, str): continue
                    chat = db.execute('SELECT * FROM desktop_chats WHERE conversation_id=?', (cid,)).fetchone()
                    if chat is None: continue
                    users = [dict(row) for row in db.execute("SELECT message_id,request_id,content,timestamp FROM desktop_messages WHERE conversation_id=? AND role='user' ORDER BY ordinal", (cid,))]
                    item = matches.setdefault(cid, dict(conversation_id=cid, reports=[], user_messages=users,
                        archived=bool(db.execute('SELECT 1 FROM desktop_archives WHERE conversation_id=?', (cid,)).fetchone()),
                        pure_fixture=False))
                    item['reports'].append(dict(path=str(path.relative_to(root)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), identity_field=key))
                    item['pure_fixture'] |= fixture_matches(key, [row['content'] for row in users])
    return dict(database=str(root/'data/kk.db'), candidates=[r for r in matches.values() if r['pure_fixture']],
        preserved_with_additional_or_nonfixture_content=[r for r in matches.values() if not r['pure_fixture']],
        policy='Exact saved harness conversation identity AND full user-message fixture sequence; no title matching')


def table_fingerprints(db):
    result = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        if table == 'desktop_archives': continue
        records = sorted(json.dumps(tuple(row), ensure_ascii=False, default=str) for row in db.execute('SELECT * FROM "'+table.replace('"', '""')+'"'))
        result[table] = dict(count=len(records), sha256=hashlib.sha256('\n'.join(records).encode()).hexdigest())
    return result


def archive(db, root, report, expected):
    candidates = report['candidates']
    if len(candidates) != expected: raise ValueError('Candidate count changed; review the audit again')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = root/'data/backups'/('acceptance-archive-'+stamp)
    backup.mkdir(parents=True)
    integrity = {}
    for name in ('kk.db', 'ambient.db'):
        source = root/'data'/name
        if not source.exists(): continue
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as live, closing(sqlite3.connect(backup/name)) as saved:
            live.backup(saved)
            integrity[name] = saved.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity[name] != 'ok': raise RuntimeError('Backup integrity check failed')
    before = table_fingerprints(db)
    original_archives = {row[0] for row in db.execute('SELECT conversation_id FROM desktop_archives')}
    newly_archived = [row['conversation_id'] for row in candidates if row['conversation_id'] not in original_archives]
    manifest = dict(source=str(root/'data'), backup=str(backup), integrity=integrity,
        newly_archived=newly_archived, original_archives=sorted(original_archives), tables_before=before,
        rollback='Run rollback-unarchive.sql against the same database to restore only these sidebar memberships; do not overwrite newer user data')
    (backup/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (backup/'provenance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (backup/'rollback-unarchive.sql').write_text('BEGIN IMMEDIATE;\n'+''.join("DELETE FROM desktop_archives WHERE conversation_id='"+cid.replace("'", "''")+"';\n" for cid in newly_archived)+'COMMIT;\n', encoding='utf-8')
    with db:
        db.execute('BEGIN IMMEDIATE')
        # Compare again inside the write transaction: never archive a fixture
        # that gained user content between its review and this operation.
        current = audit(db, root)
        if current != report: raise RuntimeError('Data/provenance changed after review; no archive applied')
        for cid in newly_archived:
            db.execute('INSERT INTO desktop_archives VALUES(?)', (cid,))
        after = table_fingerprints(db)
        if before != after: raise RuntimeError('Non-archive data changed; rollback')
        final_archives = {row[0] for row in db.execute('SELECT conversation_id FROM desktop_archives')}
        if final_archives != original_archives | set(newly_archived): raise RuntimeError('Unexpected archive change; rollback')
    result = dict(backup=str(backup), newly_archived=newly_archived, tables_unchanged=before == after,
        integrity_check=db.execute('PRAGMA integrity_check').fetchone()[0], preserved=len(report['preserved_with_additional_or_nonfixture_content']))
    (backup/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', action='store_true')
    parser.add_argument('--expected-count', type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.archive and args.expected_count is None: parser.error('--archive requires the reviewed --expected-count')
    uri = (root/'data/kk.db').as_uri()+('?mode=rw' if args.archive else '?mode=ro')
    with closing(sqlite3.connect(uri, uri=True, timeout=20)) as db:
        db.row_factory = sqlite3.Row
        report = audit(db, root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if args.archive:
            report['result'] = archive(db, root, report, args.expected_count)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(candidate_count=len(report['candidates']), preserved_count=len(report['preserved_with_additional_or_nonfixture_content']), output=str(args.output), result=report.get('result')), indent=2))


if __name__ == '__main__': main()
