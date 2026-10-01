from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import subprocess
from kk.core.desktop_settings import DesktopSettings
from kk.core.project_git import ProjectGit


class ProjectGitTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name)/'тест project';self.root.mkdir()
        self.settings=DesktopSettings(Path(self.temp.name)/'settings');self.git=ProjectGit(self.settings)
        self.git.run(self.root,['init']);self.git.run(self.root,['config','user.name','Onyx Fixture']);self.git.run(self.root,['config','user.email','fixture@example.invalid'])
        self.settings.save('git_project',str(self.root))
    def tearDown(self):self.temp.cleanup()
    def test_scoped_commit_and_changed_review_receipt(self):
        (self.root/'one.txt').write_text('one');(self.root/'two.txt').write_text('two')
        review=self.git.preview(['one.txt'],'One scoped file')
        result=self.git.commit(review['receipt'])
        self.assertTrue(result['commit'])
        self.assertEqual(self.git.run(self.root,['show','--format=','--name-only','HEAD']).strip(),'one.txt')
        self.assertEqual(self.git.rows(self.root)[0]['path'],'two.txt')
        review=self.git.preview(['two.txt'],'second')
        (self.root/'two.txt').write_text('changed')
        with self.assertRaises(ValueError):self.git.commit(review['receipt'])
    def test_preserve_foreign_staged_and_reject_secrets_hooks(self):
        (self.root/'one.txt').write_text('one');(self.root/'two.txt').write_text('two')
        self.git.run(self.root,['add','--','two.txt'])
        with self.assertRaises(ValueError):self.git.preview(['one.txt'],'one')
        self.assertEqual(self.git.run(self.root,['diff','--cached','--name-only']).strip(),'two.txt')
        (self.root/'.env').write_text('SECRET=private')
        with self.assertRaises(PermissionError):self.git.preview(['.env'],'secret')
        (self.root/'.git/hooks/pre-commit').write_text('echo unsafe')
        with self.assertRaises(PermissionError):self.git.snapshot()
    def test_offline_push_never_invokes_git_network(self):
        with self.assertRaises(PermissionError):self.git.push_preview('origin')
        with self.assertRaises(PermissionError):self.git.push('fake')
    def test_local_bare_push_contract_separate_review(self):
        (self.root/'one.txt').write_text('one')
        self.git.commit(self.git.preview(['one.txt'],'first')['receipt'])
        bare=Path(self.temp.name)/'remote.git';bare.mkdir();self.git.run(bare,['init','--bare'])
        self.git.run(self.root,['remote','add','fixture',str(bare)])
        branch=self.git.snapshot()['branch']
        self.settings.save('internet','explicit')
        self.git.run(self.root,['push','fixture','HEAD:refs/heads/'+branch])
        (self.root/'one.txt').write_text('two');self.git.commit(self.git.preview(['one.txt'],'second')['receipt'])
        self.settings.save('internet','explicit')
        review=self.git.push_preview('fixture')
        self.assertIn('second',review['outgoing'])
        self.git.push(review['receipt'])
        self.assertEqual(self.git.run(bare,['rev-parse',branch]).strip(),self.git.run(self.root,['rev-parse','HEAD']).strip())
