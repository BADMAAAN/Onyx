import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from tests.test_desktop import FakeLoop
from kk.memory.database import MemoryDatabase
from kk.core.desktop_service import DesktopService
from onyx_desktop.preferences import atomic_save,validate

class FirstSendTests(TestCase):
    def test_draft_does_not_create_chat_and_accepted_send_is_atomic_and_idempotent(self):
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'data/test.db');loop=FakeLoop();service=DesktopService(db,loop)
            try:
                cid='draft:'+'a'*32
                self.assertEqual(service.chats(),[])
                self.assertEqual(service.history(cid)['messages'],[])
                with self.assertRaises(ValueError):service.submit(cid,'valid-request','')
                self.assertEqual(service.chats(),[])
                a=service.submit(cid,'valid-request','Hello')
                b=service.submit(cid,'valid-request','Hello')
                self.assertEqual(a['task_id'],b['task_id'])
                self.assertEqual(len(service.chats()),1)
                self.assertEqual(len(service.history(cid)['messages']),2)
            finally:loop.release.set();service.close();db.close()

    def test_source_schema_has_verified_backup(self):
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'data/test.db');loop=FakeLoop();service=DesktopService(db,loop)
            try:
                self.assertTrue(list((Path(folder)/'data/backups').rglob('test.db')))
                self.assertIsNotNone(db.connection.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_sources'").fetchone())
            finally:loop.release.set();service.close();db.close()

class PreferencesTests(TestCase):
    def test_real_profile_migration_copies_and_never_overwrites(self):
        from onyx_desktop.preferences import profile_path
        with TemporaryDirectory() as folder:
            legacy=Path(folder)/'legacy.json';legacy.write_text('{"drafts":{"a":"keep"}}')
            path=profile_path('identity',legacy,folder)
            self.assertEqual(path.read_bytes(),legacy.read_bytes())
            self.assertTrue((path.parent/'preferences.pre-graphite.json').is_file())
            path.write_text('{"drafts":{"a":"new"}}')
            profile_path('identity',legacy,folder)
            self.assertIn('new',path.read_text());self.assertIn('keep',legacy.read_text())

    def test_validation_and_interrupted_replace_preserve_last_working_settings(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'prefs.json';atomic_save(path,{'textSize':15,'drafts':{'x':'keep'}})
            with patch('onyx_desktop.preferences.os.replace',side_effect=OSError('full disk')):
                with self.assertRaises(OSError):atomic_save(path,{'textSize':20})
            self.assertEqual(json.loads(path.read_text())['drafts'],{'x':'keep'})
            self.assertEqual(json.loads(path.read_text())['textSize'],15)
            for key,value in [('language','de'),('textSize',100),('glow',float('nan')),('spoken','true')]:
                with self.assertRaises(ValueError):validate(key,value)

    def test_backend_off_saved_and_device_reject_keeps_mode(self):
        from kk.core.desktop_settings import DesktopSettings
        with TemporaryDirectory() as folder:
            settings=DesktopSettings(folder);settings.save('internet','off')
            self.assertEqual(DesktopSettings(folder).get('internet'),'off')
            with patch('kk.voice.audio_devices.list_audio_devices',return_value=[]):
                with self.assertRaises(ValueError):settings.save('microphone','missing')
            self.assertEqual(DesktopSettings(folder).get('internet'),'off')
