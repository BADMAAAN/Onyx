"""Metadata and durable installation jobs; never loads weights to list models.

Ollama v0.34.4: tags/show/ps, streaming pull/create, blob HEAD/POST, delete.
All state is in the existing desktop database, behind its transaction lock.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
from threading import Event, RLock, Thread
from time import time
import uuid
from urllib.parse import urlsplit
from http.client import HTTPConnection

from kk.providers.http import request_json
from kk.core.gguf_metadata import inspect_gguf

ACTIVE = {'queued', 'running', 'cancelling'}


def model_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/-]{0,110}:[a-zA-Z0-9][a-zA-Z0-9._-]{0,60}', value):
        raise ValueError('Use an exact model:tag (no URL or command)')
    if '..' in value or value.count('/') > 1 or ':cloud' in value or '-cloud' in value:
        raise ValueError('Cloud aliases and remote registries are not local models')
    return value


def loopback(base):
    parts = urlsplit(base)
    if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1') or parts.username or parts.password or parts.path not in ('', '/'):
        raise PermissionError('Model management requires a verified loopback Ollama runtime')
    return parts


def metadata(tag, show):
    name = tag.get('name') or tag['model']
    details = show.get('details') or tag.get('details') or {}
    caps = show.get('capabilities', [])
    info = show.get('model_info') or {}
    family = str(details.get('family', ''))
    # Names and model cards are not authoritative provenance. Family labels
    # describe architecture; custom/namespace imports keep a neutral identity.
    brand = ('qwen' if family.startswith('qwen') and name.startswith('qwen') else 'openai' if family == 'gptoss' and name.startswith('gpt-oss:')
             else 'meta' if family == 'llama' and name.startswith('llama') else 'deepseek' if name.startswith('deepseek') and family.startswith('deepseek')
             else 'huggingface' if name.split(':')[0] in ('smollm','smollm2','smollm3') and family in ('llama','smollm','smollm2','smollm3') else 'custom')
    local = bool(info and tag.get('size', 0) > 0 and not show.get('remote_model') and not show.get('remote_host')
                 and not tag.get('remote_model') and not tag.get('remote_host') and 'cloud' not in name.lower())
    return dict(id=name, digest=tag.get('digest', ''), provider='ollama', brand=brand,
                publisher=dict(qwen='Qwen', openai='OpenAI / GPT-OSS', meta='Meta / Llama', deepseek='DeepSeek', huggingface='Hugging Face / SmolLM', custom='Custom / unknown')[brand],
                source='Local runtime metadata', local=local, installed=True,
                size=tag.get('size', 0), format=details.get('format', 'unknown'),
                quantization=details.get('quantization_level', 'unknown'), family=family,
                capabilities=caps, chat=local and 'completion' in caps,
                context_limit=next((v for k, v in info.items() if k.endswith('.context_length')), None),
                role='Embedding' if 'embedding' in caps and 'completion' not in caps else 'Vision / Chat' if 'vision' in caps else 'Chat',
                error='' if local else 'Local weights could not be verified; cloud inference is disabled')


class ModelManager:
    def __init__(self, desktop, provider, settings, config):
        self.desktop, self.provider, self.settings, self.config = desktop, provider, settings, config
        self.lock = RLock()
        self.rows, self.cache, self.jobs, self.cancelled, self.connections = [], {}, {}, {}, {}
        self.refreshing, self.error, self.version = False, '', ''
        self.refreshed = 0
        self.closed = Event()
        self.workers=[]
        loopback(provider.base_url)
        with desktop.lock, desktop.db:
            for row in desktop.db.execute('SELECT job_id,payload FROM desktop_model_jobs'):
                job = json.loads(row['payload'])
                if job['state'] in ACTIVE:
                    job.update(state='interrupted', status='Backend restarted; retry explicitly')
                self.jobs[row['job_id']] = job
        self.refresh()

    def api(self, path, **kwargs):
        loopback(self.provider.base_url)
        return request_json(self.provider.base_url + path, timeout=8, **kwargs)

    def refresh(self):
        with self.lock:
            if self.refreshing or self.closed.is_set(): return
            self.refreshing = True
        def work():
            try:
                version = self.api('/api/version')['version']
                tags = self.api('/api/tags')['models']
                loaded = {r['name']: r for r in self.api('/api/ps')['models']}
                rows = []
                for tag in tags[:256]:
                    key = (tag['name'], tag.get('digest'))
                    cached = self.cache.get(key)
                    if not cached or time() - cached[0] > 60:
                        cached = (time(), self.api('/api/show', method='POST', body={'model': tag['name']}))
                        self.cache[key] = cached
                    row = metadata(tag, cached[1])
                    row.update(loaded=row['id'] in loaded, vram=loaded.get(row['id'], {}).get('size_vram'),
                               loaded_context=loaded.get(row['id'], {}).get('context_length'))
                    rows.append(row)
                with self.lock:
                    self.rows, self.version, self.error, self.refreshed = rows, version, '', time()
                    self.cache = {k: v for k, v in self.cache.items() if k in {(t['name'], t.get('digest')) for t in tags}}
            except Exception as exc:
                with self.lock: self.error = str(exc)[:400]
            finally:
                with self.lock: self.refreshing = False
        worker=Thread(target=work, name='OnyxModelMetadata', daemon=True)
        self.workers.append(worker);worker.start()

    def selection(self, cid):
        with self.desktop.lock:
            row = self.desktop.db.execute('SELECT model_id,last_good FROM desktop_model_selection WHERE conversation_id=?', (cid,)).fetchone()
            revision=self.desktop.db.execute('SELECT revision FROM desktop_model_revisions WHERE conversation_id=?',(cid,)).fetchone()
        return dict(dict(row) if row else dict(model_id=self.settings.get('chat_default', self.config.get('brain.fast', self.config.get('brain.model', ''))), last_good=''), selection_revision=revision[0] if revision else 0)

    def verify(self, name, digest=None):
        # Re-read identity before every turn, including external runtime changes.
        model_name(name)
        tag = next((r for r in self.api('/api/tags')['models'] if r['name'] == name), None)
        if not tag: raise ValueError('Selected model is not installed: ' + name)
        row = metadata(tag, self.api('/api/show', method='POST', body={'model': name}))
        if not row['chat']: raise ValueError('Selected model cannot be a local Chat / Live Brain: ' + name)
        if digest and digest != row['digest']: raise ValueError('Selected model changed on disk; choose it again before sending')
        return row

    def freeze(self, cid, requested=None):
        name = requested or self.selection(cid)['model_id']
        row = self.verify(name)
        return dict(model_id=name, digest=row['digest'], capabilities=row['capabilities'],
                    options=dict(num_ctx=min(self.provider.context_window or 8192, row['context_limit'] or 8192),
                                 temperature=self.config.get('brain.temperature', .2)), provider='ollama')

    def select(self, cid, name):
        self.desktop.validate_target(cid)
        row = self.verify(name)
        with self.desktop.lock, self.desktop.db:
            self.desktop.db.execute('INSERT INTO desktop_model_selection VALUES(?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET model_id=excluded.model_id', (cid, name, ''))
            self.desktop.db.execute('INSERT INTO desktop_model_revisions VALUES(?,1) ON CONFLICT(conversation_id) DO UPDATE SET revision=revision+1',(cid,))
        return row

    def snapshot(self, cid=''):
        selected = self.selection(cid)
        with self.lock:
            rows, jobs = deepcopy(self.rows), deepcopy(list(self.jobs.values()))
            result = dict(rows=rows, jobs=jobs[-30:], refreshing=self.refreshing, error=self.error, version=self.version, refreshed=self.refreshed)
        with self.desktop.lock:
            active = {getattr(t, 'model_identity', {}).get('model_id') for t in self.desktop.tokens.values()}
        for row in rows:
            row['selected'] = row['id'] == selected['model_id']
            row['in_use'] = row['id'] in active
            row['roles'] = [role for role, key in [('Vision', 'vision.model'), ('Embedding', 'context.embedding_model')] if self.config.get(key, '') == row['id']]
            if row['id'] == self.settings.get('chat_default', self.config.get('brain.fast', self.config.get('brain.model',''))): row['roles'].append('Default Chat')
        return dict(result, **selected, conversation_id=cid, internet=self.settings.get('internet', 'off'))

    def require_network(self):
        if self.settings.get('internet', 'off') == 'off': raise PermissionError('Only local mode: enable Internet explicitly before downloading')

    def save_job(self, job):
        with self.desktop.lock, self.desktop.db:
            self.desktop.db.execute('INSERT OR REPLACE INTO desktop_model_jobs VALUES(?,?)', (job['id'], json.dumps(job)))

    def update(self, job, **fields):
        with self.lock:
            job.update(fields, updated=time())
            self.save_job(job)

    def start(self, kind, name, path='', limit=8*1024**3):
        name = model_name(name)
        if type(limit) is not int or not 1 <= limit <= 32*1024**3: raise ValueError('Download limit must be 1 byte–32 GiB')
        if kind not in ('pull', 'import'): raise ValueError('Unknown installation kind')
        if kind == 'pull': self.require_network()
        if kind == 'import':
            source = Path(path).resolve(strict=True)
            if source.stat().st_size > limit: raise ValueError('GGUF exceeds the chosen size limit')
            inspect_gguf(source)
            path = str(source)
        with self.lock:
            existing = next((j for j in self.jobs.values() if j['model'] == name and j['state'] in ACTIVE), None)
            if existing: return deepcopy(existing)
            if any(j['state'] in ACTIVE for j in self.jobs.values()): raise RuntimeError('One installation at a time; cancel or wait for the current task')
            if kind == 'import' and any(r['name'] == name for r in self.api('/api/tags')['models']): raise ValueError('Tag already exists; choose a new name')
            # Conservative free-space check; runtime storage may be configured elsewhere.
            disk_root = Path(__import__('os').environ.get('OLLAMA_MODELS', str(Path.home()/'.ollama/models')))
            while not disk_root.exists(): disk_root = disk_root.parent
            if shutil.disk_usage(disk_root).free < limit + 1024**3: raise ValueError('Not enough free disk space for the chosen limit plus 1 GiB reserve')
            job = dict(id=uuid.uuid4().hex, model=name, kind=kind, path=path, limit=limit, state='queued', status='Queued', completed=0, total=0, error='', updated=time())
            self.jobs[job['id']] = job
            self.cancelled[job['id']] = Event()
            self.save_job(job)
        worker=Thread(target=self.run, args=(job,), name='OnyxModelInstall', daemon=True)
        self.workers.append(worker);worker.start()
        return deepcopy(job)

    def check(self, job):
        if self.closed.is_set() or self.cancelled[job['id']].is_set(): raise InterruptedError('Cancelled; partial runtime blobs may be reused on explicit retry')
        if job['kind'] == 'pull': self.require_network()

    def stream(self, job, path, body):
        parts = loopback(self.provider.base_url)
        connection = HTTPConnection(parts.hostname, parts.port or 11434, timeout=30)
        with self.lock: self.connections[job['id']] = connection
        try:
            self.check(job)
            connection.request('POST', path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200: raise RuntimeError('Runtime HTTP ' + str(response.status) + ': ' + response.read(1000).decode('utf8', 'replace'))
            success = False
            layers = {}
            while True:
                self.check(job)
                line = response.readline(1024*1024)
                if not line: break
                event = json.loads(line)
                if event.get('error'): raise RuntimeError(str(event['error'])[:600])
                if event.get('digest'): layers[event['digest']] = (event.get('completed', 0), event.get('total', 0))
                total = sum(v[1] for v in layers.values())
                if total > job['limit']: raise ValueError('Runtime manifest exceeds the chosen download limit')
                self.update(job, completed=sum(v[0] for v in layers.values()), total=total, status=event.get('status', 'Working'))
                if event.get('status') == 'success': success = True; break
            if not success: raise RuntimeError('Runtime stream ended without confirmed success')
        finally:
            connection.close()
            with self.lock: self.connections.pop(job['id'], None)

    def run(self, job):
        try:
            self.update(job, state='running')
            if job['kind'] == 'pull': self.stream(job, '/api/pull', dict(model=job['model'], stream=True))
            else:
                source = Path(job['path'])
                digest = hashlib.sha256()
                with source.open('rb') as stream:
                    while chunk := stream.read(1024*1024): self.check(job); digest.update(chunk)
                key = 'sha256:' + digest.hexdigest()
                parts = loopback(self.provider.base_url)
                connection = HTTPConnection(parts.hostname, parts.port or 11434, timeout=30)
                with self.lock: self.connections[job['id']] = connection
                try:
                    connection.request('HEAD', '/api/blobs/' + key)
                    response = connection.getresponse(); response.read()
                    if response.status != 200:
                        connection.putrequest('POST', '/api/blobs/' + key)
                        connection.putheader('Content-Length', str(source.stat().st_size)); connection.endheaders()
                        completed = 0
                        with source.open('rb') as stream:
                            while chunk := stream.read(1024*1024):
                                self.check(job); connection.send(chunk); completed += len(chunk)
                                self.update(job, completed=completed, total=source.stat().st_size, status='Importing GGUF blob')
                        response = connection.getresponse(); response.read()
                        if response.status != 201: raise RuntimeError('Runtime rejected GGUF blob: ' + str(response.status))
                finally:
                    connection.close()
                    with self.lock: self.connections.pop(job['id'], None)
                self.stream(job, '/api/create', dict(model=job['model'], files={source.name: key}, stream=True))
            self.check(job)
            tags = self.api('/api/tags')['models']
            tag = next((r for r in tags if r['name'] == job['model']), None)
            if not tag: raise RuntimeError('Runtime reported success but model is missing from tags')
            details = self.api('/api/show', method='POST', body={'model': job['model']})
            if not details.get('model_info'): raise RuntimeError('Installed model has no verified local metadata')
            self.update(job, state='completed', status='Installed; not loaded or selected', digest=tag['digest'])
        except Exception as exc:
            self.update(job, state='cancelled' if self.cancelled[job['id']].is_set() else 'failed', error=str(exc)[:600], status='Installation stopped')
        finally: self.refresh()

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job['state'] not in ACTIVE: return deepcopy(job)
            self.cancelled[job_id].set()
            connection = self.connections.get(job_id)
            if connection and connection.sock:
                try: connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            self.update(job, state='cancelling', status='Cancelling owned runtime subscription')
            return deepcopy(job)

    def cancel_network(self):
        for job in list(self.jobs.values()):
            if job['kind'] == 'pull' and job['state'] in ACTIVE: self.cancel(job['id'])

    def close(self):
        self.closed.set()
        for job in list(self.jobs.values()):
            if job['state'] in ACTIVE:self.cancel(job['id'])
        for worker in self.workers:
            worker.join(10)

    def delete(self, name):
        model_name(name)
        with self.desktop.lock:
            assigned = self.desktop.db.execute('SELECT conversation_id FROM desktop_model_selection WHERE model_id=?', (name,)).fetchall()
            active = any(getattr(t, 'model_identity', {}).get('model_id') == name for t in self.desktop.tokens.values())
        roles = (self.settings.get('chat_default', self.config.get('brain.fast', '')), self.config.get('vision.model', ''), self.config.get('context.embedding_model', ''))
        if assigned or active or name in roles: raise ValueError('Model is assigned to chats, a role or an active task; choose replacements first')
        if any(j['model'] == name and j['state'] in ACTIVE for j in self.jobs.values()): raise ValueError('Installation is still active')
        manager = getattr(self.provider, 'resource_manager', None)
        if manager:
            if not manager.lock.acquire(blocking=False): raise RuntimeError('Inference is active; try again when idle')
            try:
                manager.ensure_unloaded(name)
                self.api('/api/delete', method='DELETE', body={'model': name})
            finally: manager.lock.release()
        else: self.api('/api/delete', method='DELETE', body={'model': name})
        self.refresh()
        return dict(deleted=name)

    def handle(self, request):
        op, cid = request['operation'], request.get('conversation_id', '')
        if op == 'models.refresh': self.refresh()
        elif op == 'models.inspect': return inspect_gguf(request['path'])
        elif op == 'models.select': self.select(cid, request['model'])
        elif op == 'models.default':
            self.verify(request['model']); self.settings.save('chat_default', request['model'])
        elif op == 'models.install': return self.start(request['kind'], request['model'], request.get('path', ''), request.get('limit', 8*1024**3))
        elif op == 'models.cancel': return self.cancel(request['job_id'])
        elif op == 'models.retry':
            job = self.jobs[request['job_id']]
            return self.start(job['kind'], job['model'], job['path'], job['limit'])
        elif op == 'models.delete': return self.delete(request['model'])
        elif op != 'models.list': raise ValueError('Unknown model operation')
        return self.snapshot(cid)
