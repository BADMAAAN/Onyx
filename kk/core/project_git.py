"""Explicit project scope, review receipts and separate commit/push operations."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from threading import RLock
from time import monotonic
import uuid


class ProjectGit:
    def __init__(self, settings):
        self.settings, self.lock, self.receipts = settings, RLock(), {}

    def root(self):
        value = self.settings.get('git_project', '')
        if not value: raise ValueError('Choose a project folder first')
        root = Path(value).resolve(strict=True)
        actual = Path(self.run(root, ['rev-parse', '--show-toplevel']).strip()).resolve()
        if actual != root: raise ValueError('Choose the repository root, not a child directory')
        return root

    def run(self, root, args, timeout=15):
        executable = shutil.which('git')
        if not executable: raise RuntimeError('Git is not installed')
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat')
        command=[executable, '-C', str(root), *args]
        if args[0]=='push':
            result=self.network_run(command,root,timeout,env)
        else:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=timeout,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode: raise RuntimeError(result.stderr.strip()[:1500] or 'Git command failed')
        return result.stdout

    def network_run(self, command, root, timeout, env=None):
        if self.settings.get('internet','off')=='off': raise PermissionError('Network operation is disabled in local mode')
        process=subprocess.Popen(command,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=monotonic()+timeout
        try:
            while True:
                if self.settings.get('internet','off')=='off': raise PermissionError('Network operation cancelled: local mode enabled')
                if monotonic()>deadline: raise TimeoutError('Network operation timed out')
                try:
                    stdout,stderr=process.communicate(timeout=.2)
                    return subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
                except subprocess.TimeoutExpired: pass
        finally:
            if process.poll() is None:
                import psutil
                try:
                    for child in reversed(psutil.Process(process.pid).children(recursive=True)):
                        try: child.kill()
                        except psutil.NoSuchProcess: pass
                except psutil.NoSuchProcess: pass
                process.kill();process.communicate()

    def trusted_automation(self, root):
        # Hooks, filters, signers and helpers can execute code/network. Never
        # silently disable them with --no-verify or claim Windows isolation.
        hooks = Path(self.run(root, ['rev-parse', '--git-path', 'hooks']).strip())
        if not hooks.is_absolute(): hooks = root/hooks
        config = self.run(root, ['config', '--list'])
        if any(re.match(r'(?i)(core\.hookspath|core\.fsmonitor|commit\.gpgsign|gpg\.program|core\.sshcommand)=', line) and line.rsplit('=',1)[-1].lower() not in ('false','0','') for line in config.splitlines()):
            raise PermissionError('External Git automation is configured; review/run it in your terminal')
        attribute_paths=[root/p for p in self.run(root,['ls-files','--cached','--others','--exclude-standard','--','**/.gitattributes','.gitattributes']).splitlines()]
        gitdir=Path(self.run(root,['rev-parse','--absolute-git-dir']).strip())
        attribute_paths.append(gitdir/'info/attributes')
        for line in config.splitlines():
            if line.startswith('core.attributesfile='):attribute_paths.append(Path(line.split('=',1)[1]).expanduser())
        if any(p.is_file() and re.search(r'\bfilter=',p.read_text(encoding='utf-8',errors='replace')) for p in attribute_paths):
            raise PermissionError('Git attributes configure executable filters; review them in your terminal')
        if hooks.is_dir() and any(p.is_file() and not p.name.endswith('.sample') for p in hooks.iterdir()):
            raise PermissionError('Repository hooks need separate review in your terminal')

    def rows(self, root):
        raw = self.run(root, ['status', '--porcelain=v1', '-z', '--untracked-files=all'])
        entries = raw.split('\0'); rows = []; i = 0
        while i < len(entries):
            entry = entries[i]; i += 1
            if not entry: continue
            status, path = entry[:2], entry[3:]
            old = ''
            if 'R' in status or 'C' in status: old = entries[i]; i += 1
            rows.append(dict(path=path, status=status, old_path=old))
        return rows

    def snapshot(self):
        root = self.root()
        self.trusted_automation(root)
        branch = self.run(root, ['symbolic-ref', '--short', 'HEAD']).strip()
        rows = self.rows(root)
        remotes = []
        for name in self.run(root, ['remote']).splitlines():
            url = self.run(root, ['remote', 'get-url', '--push', name]).strip()
            safe = not re.search(r'://[^/]*@', url)
            remotes.append(dict(name=name, url=url if safe else '[credentials in URL: fix remote configuration]', safe=safe))
        auth = 'Not checked in local mode'
        if self.settings.get('internet', 'off') != 'off' and shutil.which('gh'):
            result = self.network_run([shutil.which('gh'), 'auth', 'status'],root,12)
            auth = 'GitHub CLI authenticated' if result.returncode == 0 else 'GitHub CLI not authenticated; use gh auth login in your terminal'
        return dict(root=str(root), branch=branch, files=rows, remotes=remotes, auth=auth, installed=True)

    def safe_files(self, root, paths):
        if not isinstance(paths, list) or not paths or len(paths) > 100 or any(not isinstance(p,str) for p in paths): raise ValueError('Select 1–100 specific files')
        changes = {row['path']: row for row in self.rows(root)}
        for relative in paths:
            path = (root/relative).resolve()
            if not path.is_relative_to(root) or relative not in changes or relative.startswith('-'): raise ValueError('Invalid project file selection')
            if changes[relative]['old_path']: raise ValueError('Review renames in the terminal')
            if re.search(r'(?i)(^|/)(?:\.env(?:\..*)?|id_rsa|credentials.*|.*\.pem|.*\.key|.*\.db|.*\.sqlite.*|.*\.wav|.*\.gguf|dist|\.venv)(/|$)', relative):
                raise PermissionError('Sensitive/generated file excluded: ' + relative)
            if path.exists():
                if not path.is_file() or path.stat().st_size > 2*1024**2: raise ValueError('Review binary/large files in the terminal')
                content = path.read_bytes()
                if b'\0' in content or re.search(rb'(?:-----BEGIN .*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|(?i:api[_-]?key|token|password)\s*[=:]\s*[\x22\x27][^\x22\x27\r\n]{12,})', content):
                    raise PermissionError('Potential secret/binary excluded: ' + relative)
        staged = set(self.run(root, ['diff', '--cached', '--name-only', '-z']).split('\0')) - {''}
        if staged - set(paths): raise ValueError('Other files are already staged; preserve/review them first')
        return paths

    def fingerprint(self, root, paths):
        state = [self.run(root, ['status', '--porcelain=v1', '-z']), self.run(root, ['diff', '--no-ext-diff', '--no-textconv', '--binary']), self.run(root, ['diff', '--cached', '--no-ext-diff', '--no-textconv', '--binary'])]
        for p in paths:
            path = root/p
            state.append(hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'deleted')
        return hashlib.sha256(json.dumps(state).encode()).hexdigest()

    def preview(self, paths, message):
        with self.lock:
            root = self.root(); self.trusted_automation(root); self.safe_files(root, paths)
            if not isinstance(message,str) or not message.strip() or len(message)>2000: raise ValueError('Enter a commit message')
            info = self.snapshot()
            diff = self.run(root, ['diff', 'HEAD', '--no-ext-diff', '--no-textconv', '--', *paths]) if self.run(root, ['rev-list', '--all', '--count']).strip() != '0' else self.run(root, ['diff', '--cached', '--no-ext-diff', '--no-textconv', '--', *paths])
            for path in paths:
                if next(r for r in info['files'] if r['path']==path)['status']=='??': diff += '\nNew file: '+path+'\n'+(root/path).read_text(encoding='utf-8',errors='replace')
            receipt = uuid.uuid4().hex
            self.receipts[receipt] = dict(kind='commit', root=str(root), paths=paths, message=message, fingerprint=self.fingerprint(root, paths), expires=monotonic()+300)
            return dict(receipt=receipt, root=str(root), branch=info['branch'], paths=paths, message=message, diff=diff[:100000], truncated=len(diff)>100000)

    def commit(self, receipt):
        with self.lock:
            review = self.receipts.pop(receipt, None)
            if not review or review['kind']!='commit' or review['expires']<monotonic(): raise ValueError('Review expired; preview again')
            root = self.root(); self.trusted_automation(root); self.safe_files(root,review['paths'])
            if str(root)!=review['root'] or self.fingerprint(root,review['paths'])!=review['fingerprint']: raise ValueError('Project changed; review the new diff first')
            self.run(root, ['add', '--', *review['paths']])
            output = self.run(root, ['commit', '--only', '-m', review['message'], '--', *review['paths']], timeout=30)
            return dict(commit=self.run(root, ['rev-parse','HEAD']).strip(), output=output)

    def push_preview(self, remote):
        if self.settings.get('internet','off')=='off': raise PermissionError('Push is disabled in local mode')
        root=self.root(); info=self.snapshot()
        if remote not in [r['name'] for r in info['remotes'] if r['safe']]: raise ValueError('Choose a configured remote without embedded credentials')
        branch=info['branch']
        # A cached remote tracking ref is necessary; no implicit fetch/publication.
        upstream=remote+'/'+branch
        outgoing=self.run(root,['log','--oneline',upstream+'..HEAD'])
        if not outgoing.strip(): raise ValueError('No outgoing commits relative to the local tracking ref')
        receipt=uuid.uuid4().hex
        self.receipts[receipt]=dict(kind='push',root=str(root),remote=remote,branch=branch,head=self.run(root,['rev-parse','HEAD']).strip(),url=next(r['url'] for r in info['remotes'] if r['name']==remote),expires=monotonic()+300)
        return dict(receipt=receipt,root=str(root),remote=remote,branch=branch,outgoing=outgoing,basis='Local tracking ref; remote may have advanced')

    def push(self,receipt):
        with self.lock:
            if self.settings.get('internet','off')=='off': raise PermissionError('Push is disabled in local mode')
            review=self.receipts.pop(receipt,None)
            if not review or review['kind']!='push' or review['expires']<monotonic():raise ValueError('Push review expired')
            root=self.root();self.trusted_automation(root)
            if str(root)!=review['root'] or self.run(root,['rev-parse','HEAD']).strip()!=review['head'] or self.run(root,['symbolic-ref','--short','HEAD']).strip()!=review['branch'] or self.run(root,['remote','get-url','--push',review['remote']]).strip()!=review['url']:raise ValueError('Push target changed; review again')
            return dict(output=self.run(root,['push',review['remote'],'HEAD:refs/heads/'+review['branch']],timeout=45))

    def handle(self, request):
        op=request['operation']
        if op=='git.select':self.settings.save('git_project',request['path']);return self.snapshot()
        if op=='git.status':return self.snapshot()
        if op=='git.preview':return self.preview(request['paths'],request['message'])
        if op=='git.commit':return self.commit(request['receipt'])
        if op=='git.pushPreview':return self.push_preview(request['remote'])
        if op=='git.push':return self.push(request['receipt'])
        raise ValueError('Unknown Git operation')
