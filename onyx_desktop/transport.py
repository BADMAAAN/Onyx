"""Versioned authenticated loopback client. Never replay mutating requests."""
import json
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler
import psutil


class LocalTransport:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.metadata = None
        self.opener = build_opener(ProxyHandler({}))

    def connect(self):
        metadata = json.loads((self.root/'data'/'runtime_control.json').read_text(encoding='utf-8'))
        try: created=psutil.Process(metadata['pid']).create_time()
        except psutil.NoSuchProcess: raise FileNotFoundError('Backend process has exited') from None
        if abs(created-metadata['created'])>.01: raise FileNotFoundError('Stale backend identity')
        if metadata.get('protocol') != 1: raise RuntimeError('Incompatible backend. Restart the updated Onyx backend.')
        if not 1<=int(metadata['port'])<=65535: raise RuntimeError('Invalid local endpoint')
        self.metadata = metadata
        hello = self.call('hello')
        if hello.get('protocol') != 1 or hello.get('identity') != metadata.get('identity'):
            self.metadata = None
            raise RuntimeError('Backend identity/protocol mismatch')
        root = hello.get('build',{}).get('data_root') or hello.get('build',{}).get('root')
        if root and Path(root).resolve() != self.root: raise RuntimeError('Backend belongs to another installation')
        return hello

    def call(self, operation, **arguments):
        if self.metadata is None: raise RuntimeError('Not connected')
        request = Request(f"http://127.0.0.1:{int(self.metadata['port'])}/control",
            data=json.dumps(dict(operation=operation,**arguments),ensure_ascii=False).encode('utf-8'),
            headers={'Authorization':'Bearer '+self.metadata['token'],'Content-Type':'application/json'},method='POST')
        with self.opener.open(request,timeout=60 if operation.startswith('git.') else 20 if operation.startswith('models.') else 3) as response: result=json.load(response)
        if not result.get('ok'): raise RuntimeError(result.get('error','Backend rejected request'))
        return result['data']
