import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.audit_acceptance_provenance import AMBER, FOLLOWUP, VIOLET, archive, audit


class AcceptanceProvenanceTests(TestCase):
    def test_saved_identity_and_full_content_are_required_and_archive_preserves_data(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = root/'data';data.mkdir()
            report = data/'desktop_acceptance/run';report.mkdir(parents=True)
            (report/'gui-acceptance.json').write_text(json.dumps({'conversation_a':'extended', 'conversation_b':'pure'}))
            db = sqlite3.connect(data/'kk.db');db.row_factory = sqlite3.Row
            try:
                db.executescript('''CREATE TABLE desktop_chats(conversation_id TEXT PRIMARY KEY, title TEXT);
                    CREATE TABLE desktop_archives(conversation_id TEXT PRIMARY KEY);
                    CREATE TABLE desktop_messages(ordinal INTEGER PRIMARY KEY, conversation_id TEXT, message_id TEXT, request_id TEXT, role TEXT, content TEXT, timestamp TEXT);''')
                for cid in ('pure','extended','same-title','same-content'):
                    db.execute('INSERT INTO desktop_chats VALUES(?,?)',(cid,AMBER))
                for i,(cid,text) in enumerate((('pure',AMBER),('extended',VIOLET),('extended',FOLLOWUP),('extended','My later personal message'),('same-title','Real user content'),('same-content',AMBER))):
                    db.execute('INSERT INTO desktop_messages VALUES(?,?,?,?,?,?,?)',(i,cid,str(i),str(i),'user',text,'date'))
                db.commit()
                result = audit(db,root)
                self.assertEqual([r['conversation_id'] for r in result['candidates']],['pure'])
                self.assertEqual([r['conversation_id'] for r in result['preserved_with_additional_or_nonfixture_content']],['extended'])
                outcome = archive(db,root,result,1)
                self.assertTrue(outcome['tables_unchanged'])
                self.assertEqual(db.execute('SELECT count(*) FROM desktop_messages').fetchone()[0],6)
                self.assertEqual(db.execute('SELECT count(*) FROM desktop_chats').fetchone()[0],4)
                self.assertEqual([row[0] for row in db.execute('SELECT conversation_id FROM desktop_archives')],['pure'])
                rollback = Path(outcome['backup'])/'rollback-unarchive.sql'
                db.executescript(rollback.read_text())
                self.assertEqual(db.execute('SELECT count(*) FROM desktop_archives').fetchone()[0],0)
                with self.assertRaises(ValueError): archive(db,root,result,2)
            finally:
                db.close()
