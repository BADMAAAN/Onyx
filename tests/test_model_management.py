import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch

from kk.core.desktop_service import DesktopService
from kk.core.desktop_settings import DesktopSettings
from kk.core.model_management import ModelManager, metadata, model_name, loopback
from kk.memory.database import MemoryDatabase
from tests.test_desktop import FakeLoop


class ModelsTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
        self.memory=MemoryDatabase(self.root/'data/test.db');self.loop=FakeLoop();self.service=DesktopService(self.memory,self.loop)
        self.settings=DesktopSettings(self.root)
        self.tags=[dict(name=n,digest=n+'-digest',size=100,details=dict(family='qwen3',format='gguf',quantization_level='Q4_K_M')) for n in ('qwen3:1b','qwen3:2b','embed:1b','alias:latest')]
        self.calls=[]
        def api(path,**kwargs):
            self.calls.append((path,kwargs))
            if path.endswith('/api/version'):return {'version':'0.34.4'}
            if path.endswith('/api/tags'):return {'models':self.tags}
            if path.endswith('/api/ps'):return {'models':[dict(name='qwen3:2b',size_vram=70,context_length=4096)]}
            if path.endswith('/api/show'):
                n=kwargs['body']['model'];row=dict(model_info={'qwen3.context_length':32768},capabilities=['embedding'] if n.startswith('embed') else ['completion','tools'])
                if n.startswith('alias'):row['remote_model']='remote-model';row['remote_host']='https://ollama.com'
                return row
            raise AssertionError(path)
        self.patch=patch('kk.core.model_management.request_json',side_effect=api);self.patch.start()
        self.manager=ModelManager(self.service,NS(base_url='http://127.0.0.1:11434',context_window=4096),self.settings,NS(get=lambda k,d=None:{'brain.fast':'qwen3:1b','brain.temperature':.2}.get(k,d)))
        self.service.models=self.manager
        deadline=monotonic()+2
        while self.manager.refreshing and monotonic()<deadline:sleep(.01)
        self.cid='draft:'+'a'*32

    def tearDown(self):
        self.manager.close();self.loop.release.set();self.service.close();self.patch.stop();self.memory.close();self.temp.cleanup()

    def test_installed_selected_loaded_embedding_and_cloud_are_distinct(self):
        rows={r['id']:r for r in self.manager.snapshot(self.cid)['rows']}
        self.assertTrue(rows['qwen3:1b']['selected']);self.assertFalse(rows['qwen3:1b']['loaded'])
        self.assertTrue(rows['qwen3:2b']['loaded']);self.assertFalse(rows['qwen3:2b']['selected'])
        self.assertFalse(rows['embed:1b']['chat']);self.assertFalse(rows['alias:latest']['chat'])
        self.assertFalse(any(p.endswith('/api/chat') for p,_ in self.calls))
        for name in ('embed:1b','alias:latest','missing:1b'):
            with self.assertRaises(ValueError):self.manager.select(self.cid,name)

    def test_switch_freezes_inflight_identity_and_does_not_clear_history(self):
        self.manager.select(self.cid,'qwen3:1b')
        self.service.submit(self.cid,'request-first','one',model_id='qwen3:1b')
        self.loop.started.wait(1)
        self.manager.select(self.cid,'qwen3:2b')
        self.assertEqual(self.service.tokens['request-first'].model_identity['model_id'],'qwen3:1b')
        self.service.submit(self.cid,'request-second','two',model_id='qwen3:2b')
        self.assertEqual(self.service.tokens['request-second'].model_identity['model_id'],'qwen3:2b')
        history=self.service.history(self.cid)['messages']
        self.assertEqual(len(history),4)
        self.assertEqual(history[0]['model_identity']['model_id'],'qwen3:1b')
        self.assertEqual(history[2]['model_identity']['model_id'],'qwen3:2b')
        with self.assertRaises(ValueError):self.service.submit(self.cid,'request-first','one',model_id='qwen3:2b')

    def test_reject_before_accept_preserves_draft_scope(self):
        with self.assertRaises(ValueError):self.service.submit(self.cid,'request-bad-model','draft',model_id='embed:1b')
        self.assertEqual(self.service.history(self.cid)['messages'],[])
        self.assertEqual(self.service.chats(),[])

    def test_actual_model_requires_completed_runtime_response_metadata(self):
        self.service.submit(self.cid,'request-metadata','one',model_id='qwen3:1b')
        self.service.event('request-metadata',dict(event='brain_request',model='qwen3:1b',options={'num_ctx':4096}))
        identity=self.service.history(self.cid)['messages'][0]['model_identity']
        self.assertEqual(identity['requested_model'],'qwen3:1b')
        self.assertNotIn('actual_model',identity)
        for event in (dict(event='brain_response',model='qwen3:1b',done=False), dict(event='brain_response',model=None,done=True)):
            self.service.event('request-metadata',event)
            self.assertNotIn('actual_model',self.service.history(self.cid)['messages'][0]['model_identity'])
        self.service.event('request-metadata',dict(event='brain_response',model='qwen3:1b',done=True))
        identity=self.service.history(self.cid)['messages'][0]['model_identity']
        self.assertEqual(identity['actual_model'],'qwen3:1b')
        self.assertTrue(identity['response_completed'])

    def test_offline_rejects_before_pull_or_cloud_call(self):
        with self.assertRaises(PermissionError):self.manager.start('pull','qwen3:0.6b')
        self.assertFalse(any(p.endswith('/api/pull') for p,_ in self.calls))
        with self.assertRaises(ValueError):self.manager.freeze(self.cid,'alias:latest')
        with self.assertRaises(PermissionError):loopback('https://example.com')

    def test_import_checks_header_extension_collision_and_limit(self):
        file=self.root/'weights.gguf';file.write_bytes(b'GGUF'+b'\0'*50)
        with self.assertRaises(ValueError):self.manager.start('import','qwen3:1b',str(file),1000)
        with self.assertRaises(ValueError):self.manager.start('import','new:1b',str(file),10)
        file.write_bytes(b'bad!')
        with self.assertRaises(ValueError):self.manager.start('import','new:1b',str(file),1000)
        for n in ('model','../model:1','https://host/model:1','test:cloud'):
            with self.assertRaises(ValueError):model_name(n)

    def test_digest_change_rejected_and_selection_durable(self):
        self.manager.select(self.cid,'qwen3:2b')
        frozen=self.manager.freeze(self.cid)
        self.tags[1]['digest']='new-digest'
        with self.assertRaises(ValueError):self.manager.verify(frozen['model_id'],frozen['digest'])
        self.assertEqual(self.memory.connection.execute('SELECT model_id FROM desktop_model_selection WHERE conversation_id=?',(self.cid,)).fetchone()[0],'qwen3:2b')

    def test_metadata_cache_refresh_detects_external_install(self):
        before=len([p for p,_ in self.calls if p.endswith('/api/show')])
        self.tags.append(dict(self.tags[0],name='qwen3:new',digest='new'))
        self.manager.refresh()
        deadline=monotonic()+2
        while self.manager.refreshing and monotonic()<deadline:sleep(.01)
        self.assertEqual(len(self.manager.rows),5)
        self.assertEqual(len([p for p,_ in self.calls if p.endswith('/api/show')]),before+1)

    def test_completed_jobs_require_metadata_and_do_not_select(self):
        self.settings.save('internet','explicit')
        with patch.object(self.manager,'stream',return_value=None):
            job=self.manager.start('pull','qwen3:1b',limit=1000)
            deadline=monotonic()+2
            while self.manager.jobs[job['id']]['state'] in ('queued','running') and monotonic()<deadline:sleep(.01)
        self.assertEqual(self.manager.jobs[job['id']]['state'],'completed')
        self.assertEqual(self.service.db.execute('SELECT count(*) FROM desktop_model_selection').fetchone()[0],0)

