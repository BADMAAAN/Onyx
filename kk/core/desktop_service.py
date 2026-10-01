"""Durable desktop protocol, feeding the existing VoiceLoop executor.

No model, audio device or second execution loop lives here. The small dispatcher
only admits one request at a time to the production command worker.
"""
from __future__ import annotations

import hashlib
import json
from queue import Queue, Empty
from threading import RLock, Thread, Event
from time import monotonic
import uuid
import re

from kk.core.task import CancellationToken
from kk.memory.database import utc_now

PROTOCOL = 1
ACTIVE = ('queued', 'running', 'cancelling')


class DesktopService:
    def __init__(self, memory, loop):
        self.memory, self.loop = memory, loop
        self.lock, self.closed = RLock(), Event()
        self.queue, self.tokens = Queue(maxsize=32), {}
        self._migrate()
        loop.desktop_service = self
        loop.agent.orchestrator.conversation_store = self
        self.thread = Thread(target=self._dispatch, name='OnyxAdmission', daemon=True)
        self.thread.start()

    @property
    def db(self):
        return self.memory.connection

    def _migrate(self):
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_chats'").fetchone()
        archives_exist = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_archives'").fetchone()
        sources_exist = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_sources'").fetchone()
        tombstones_exist = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_deleted'").fetchone()
        models_exist = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_model_turns'").fetchone()
        projects_exist = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_projects'").fetchone()
        if not exists or not archives_exist or not sources_exist or not tombstones_exist or not models_exist or not projects_exist:
            # Back up the live database using SQLite's supported online API.
            import sqlite3
            destination = self.memory.path.parent / 'backups' / ('desktop-' + uuid.uuid4().hex)
            destination.mkdir(parents=True, exist_ok=True)
            from contextlib import closing
            with closing(sqlite3.connect(destination / self.memory.path.name)) as saved:
                self.db.backup(saved)
                if saved.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Desktop migration backup failed')
            (destination/'manifest.json').write_text(json.dumps(dict(source=str(self.memory.path.resolve()),backup=str(destination/self.memory.path.name),integrity_check='ok',reason='desktop schema migration'),indent=2),encoding='utf-8')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS desktop_chats(
                conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'chat', workspace_path TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                renamed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS desktop_requests(
                request_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                source TEXT NOT NULL, spoken INTEGER NOT NULL,
                task_id TEXT, error TEXT, revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS desktop_messages(
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL,
                conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                timestamp TEXT NOT NULL, status TEXT NOT NULL,
                request_id TEXT, task_id TEXT, source TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1);
            CREATE INDEX IF NOT EXISTS desktop_messages_chat ON desktop_messages(conversation_id,ordinal);
            CREATE TABLE IF NOT EXISTS desktop_archives(conversation_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS desktop_sources(request_id TEXT PRIMARY KEY, status TEXT NOT NULL, sources TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desktop_pins(conversation_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS desktop_deleted(conversation_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desktop_model_selection(conversation_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, last_good TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS desktop_model_turns(request_id TEXT PRIMARY KEY, identity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desktop_model_jobs(job_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desktop_projects(project_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                workspace_path TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desktop_chat_projects(conversation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS desktop_model_revisions(conversation_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
        ''')
        if not exists:
            # Preserve every historical scope and stable source row ID. No merge
            # of unrelated sessions and no mutation of the historical table.
            self.db.execute('''INSERT OR IGNORE INTO desktop_chats
                SELECT session_id, 'Legacy · ' || substr(session_id,1,8), 'chat', NULL,
                       min(created_at),max(created_at),1 FROM conversations GROUP BY session_id''')
            self.db.execute('''INSERT OR IGNORE INTO desktop_messages
                (message_id,conversation_id,role,content,timestamp,status,source)
                SELECT 'legacy:' || id,session_id,role,content,created_at,'completed','legacy'
                FROM conversations ORDER BY id''')
        self.db.execute("UPDATE desktop_requests SET status='interrupted',error='Backend restarted',revision=revision+1 WHERE status IN ('queued','running','cancelling')")
        self.db.execute("UPDATE desktop_messages SET status='interrupted',version=version+1 WHERE role='assistant' AND status IN ('queued','running','cancelling')")
        self.db.commit()

    def create(self, title='New chat', conversation_id=None):
        cid, now = conversation_id or uuid.uuid4().hex, utc_now()
        with self.lock, self.db:
            if self.is_deleted(cid): raise ValueError('Conversation was deleted')
            self.db.execute('INSERT OR IGNORE INTO desktop_chats VALUES(?,?,?,NULL,?,?,0)',
                            (cid, title[:100], 'chat', now, now))
        return dict(conversation_id=cid)

    def chats(self, offset=0, archived=False):
        return [dict(r) for r in self.db.execute('''SELECT *,
            (SELECT project_id FROM desktop_chat_projects p WHERE p.conversation_id=desktop_chats.conversation_id) AS project_id,
            (conversation_id IN (SELECT conversation_id FROM desktop_pins)) AS pinned FROM desktop_chats WHERE
            (conversation_id IN (SELECT conversation_id FROM desktop_archives))=?
            ORDER BY pinned DESC,updated_at DESC LIMIT 100 OFFSET ?''', (int(bool(archived)),max(0,int(offset))))]

    def is_deleted(self,cid):
        return bool(self.db.execute('SELECT 1 FROM desktop_deleted WHERE conversation_id=?',(cid,)).fetchone())

    def projects(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM desktop_projects ORDER BY title COLLATE NOCASE')]

    def save_project(self, title, project_id=None, workspace_path=None):
        if not isinstance(title,str) or not title.strip():raise ValueError('Project title is empty')
        if workspace_path:
            from pathlib import Path
            path=Path(workspace_path).resolve(strict=True)
            if not path.is_dir():raise ValueError('Workspace must be a directory')
            workspace_path=str(path)
        with self.lock,self.db:
            now=utc_now()
            if project_id:
                if not self.db.execute('SELECT 1 FROM desktop_projects WHERE project_id=?',(project_id,)).fetchone():raise ValueError('Unknown project')
                self.db.execute('UPDATE desktop_projects SET title=?,updated_at=? WHERE project_id=?',(title.strip()[:100],now,project_id))
            else:
                project_id=uuid.uuid4().hex
                self.db.execute('INSERT INTO desktop_projects VALUES(?,?,?,?,?)',(project_id,title.strip()[:100],workspace_path or None,now,now))
        return self.projects()

    def move_to_project(self,cid,project_id):
        with self.lock,self.db:
            self.validate_target(cid)
            if project_id:
                if not self.db.execute('SELECT 1 FROM desktop_projects WHERE project_id=?',(project_id,)).fetchone():raise ValueError('Unknown project')
                self.db.execute('INSERT INTO desktop_chat_projects VALUES(?,?) ON CONFLICT(conversation_id) DO UPDATE SET project_id=excluded.project_id',(cid,project_id))
            else:self.db.execute('DELETE FROM desktop_chat_projects WHERE conversation_id=?',(cid,))
        return dict(conversation_id=cid,project_id=project_id)

    def validate_target(self,cid):
        if self.is_deleted(cid): raise ValueError('Conversation was deleted')
        if not self.db.execute('SELECT 1 FROM desktop_chats WHERE conversation_id=?',(cid,)).fetchone() and not re.fullmatch(r'draft:[0-9a-f]{32}',str(cid)):
            raise ValueError('Unknown conversation')

    def pin(self,cid,pinned):
        if type(pinned) is not bool: raise ValueError('pinned must be boolean')
        with self.lock,self.db:
            if not self.db.execute('SELECT 1 FROM desktop_chats WHERE conversation_id=?',(cid,)).fetchone():raise ValueError('Unknown conversation')
            if pinned:self.db.execute('INSERT OR IGNORE INTO desktop_pins VALUES(?)',(cid,))
            else:self.db.execute('DELETE FROM desktop_pins WHERE conversation_id=?',(cid,))
        return dict(conversation_id=cid,pinned=pinned)

    def delete(self,cid):
        # Same lock order as admission: input owner then history owner.
        with self.loop.input_lock,self.lock:
            self.validate_target(cid)
            for rid,token in list(self.tokens.items()):
                if token.session_id==cid:self.cancel(rid)
            if getattr(self.loop,'desktop_live_scope',None)==cid:
                from kk.voice.dictation import stop_live
                stop_live(self.loop)
            dictation=getattr(self.loop,'dictation',None)
            if dictation and dictation.active and dictation.sessions[dictation.active]['conversation_id']==cid:
                dictation.handle('dictation.cancel',dict(session_id=dictation.active))
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO desktop_deleted VALUES(?,?)',(cid,utc_now()))
                self.db.execute('DELETE FROM desktop_sources WHERE request_id IN (SELECT request_id FROM desktop_requests WHERE conversation_id=?)',(cid,))
                self.db.execute('DELETE FROM desktop_model_turns WHERE request_id IN (SELECT request_id FROM desktop_requests WHERE conversation_id=?)',(cid,))
                self.db.execute('DELETE FROM desktop_model_selection WHERE conversation_id=?',(cid,))
                for table in ('desktop_messages','desktop_requests','desktop_archives','desktop_pins','desktop_chats','desktop_chat_projects','desktop_model_revisions'):
                    self.db.execute(f'DELETE FROM {table} WHERE conversation_id=?',(cid,))
                self.db.execute('DELETE FROM conversations WHERE session_id=?',(cid,))
            # In-memory retrieval cache must not retain this scope.
            owner=self.loop.agent.orchestrator
            getattr(owner,'_conversation_contexts',{}).pop(cid,None)
            if getattr(owner,'_active_scope',None)==cid:
                owner._active_scope=None
        return dict(conversation_id=cid,deleted=True,retained='Global preferences, task journal and backups')

    def archive(self,cid,archived):
        if not isinstance(archived,bool):raise ValueError('archived must be boolean')
        with self.lock,self.db:
            if not self.db.execute('SELECT 1 FROM desktop_chats WHERE conversation_id=?',(cid,)).fetchone():raise ValueError('Unknown conversation')
            if archived:self.db.execute('INSERT OR IGNORE INTO desktop_archives VALUES(?)',(cid,))
            else:self.db.execute('DELETE FROM desktop_archives WHERE conversation_id=?',(cid,))
        return dict(conversation_id=cid,archived=archived)

    def rename(self, cid, title):
        if not isinstance(title, str) or not title.strip(): raise ValueError('Title is empty')
        with self.lock, self.db:
            self.db.execute('UPDATE desktop_chats SET title=?,renamed=1 WHERE conversation_id=?', (title.strip()[:100],cid))
        return dict(conversation_id=cid)

    def history(self, cid, before=0, limit=60, after=None):
        signature = ':'.join(str(v) for v in self.db.execute(
            'SELECT count(*),coalesce(max(ordinal),0),coalesce(sum(version),0) FROM desktop_messages WHERE conversation_id=?',(cid,)).fetchone())
        if not before and after == signature: return dict(conversation_id=cid,unchanged=True,revision=signature)
        limit = max(1,min(100,int(limit)))
        rows = self.db.execute('''SELECT * FROM desktop_messages WHERE conversation_id=?
            AND (?=0 OR ordinal<?) ORDER BY ordinal DESC LIMIT ?''', (cid,int(before),int(before),limit)).fetchall()
        messages = [dict(r) for r in reversed(rows)]
        for message in messages:
            identity=self.db.execute('SELECT identity FROM desktop_model_turns WHERE request_id=?',(message['request_id'],)).fetchone()
            message['model_identity']=json.loads(identity[0]) if identity else {}
            provenance=self.db.execute('SELECT status,sources FROM desktop_sources WHERE request_id=?',(message['request_id'],)).fetchone()
            message['provenance']=provenance['status'] if provenance else 'local'
            message['sources']=json.loads(provenance['sources']) if provenance else []
        return dict(conversation_id=cid, messages=messages, revision=signature, has_more=len(rows)==limit,
                    requests=[dict(r) for r in self.db.execute('SELECT * FROM desktop_requests WHERE conversation_id=? AND status IN (?,?,?)',(cid,*ACTIVE))])

    def prompt_history(self, cid):
        rows = self.db.execute('''SELECT role,content FROM desktop_messages WHERE conversation_id=?
            AND status='completed' ORDER BY ordinal DESC LIMIT 24''', (cid,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def request(self, rid):
        row = self.db.execute('SELECT * FROM desktop_requests WHERE request_id=?',(rid,)).fetchone()
        if not row: return dict(request_id=rid, status='not_found')
        result = dict(row)
        result['messages'] = [dict(r) for r in self.db.execute('SELECT * FROM desktop_messages WHERE request_id=? ORDER BY ordinal',(rid,))]
        return result

    def submit(self, cid, rid, text, *, source='text', spoken=False, evidence=None, model_id=None):
        if not isinstance(text,str) or not text.strip() or len(text)>16000: raise ValueError('Expected 1–16000 characters')
        if not isinstance(rid,str) or not 8<=len(rid)<=100: raise ValueError('Invalid client_request_id')
        if source not in ('text','voice'): raise ValueError('Invalid source')
        fingerprint = hashlib.sha256(json.dumps([cid,text,source,bool(spoken),model_id],ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            self.validate_target(cid)
            existing = self.request(rid)
            if existing['status'] != 'not_found':
                if existing['fingerprint'] != fingerprint: raise ValueError('Request ID was already used with another payload')
                return existing
            if self.closed.is_set() or self.queue.full(): raise RuntimeError('Request queue is unavailable or full')
            manager = getattr(self, 'models', None)
            identity = manager.freeze(cid, model_id) if manager else None
            exists = self.db.execute('SELECT 1 FROM desktop_chats WHERE conversation_id=?',(cid,)).fetchone()
            if not exists and not re.fullmatch(r'draft:[0-9a-f]{32}', str(cid)): raise ValueError('Unknown conversation')
            token = CancellationToken(session_id=cid)
            token.trace_id = token.generation_id
            token.history_managed = True
            token.source = source
            token.request_id = rid
            if identity: token.model_identity = identity
            token.on_content = lambda chunk: self.append(rid, chunk)
            token.on_event = lambda event: self.event(rid,event)
            token.on_complete = lambda: self.finish(rid,token)
            now = utc_now()
            with self.db:
                if not exists:
                    self.db.execute('INSERT INTO desktop_chats VALUES(?,?,?,NULL,?,?,0)',
                                    (cid, 'New chat', 'chat', now, now))
                self.db.execute('INSERT INTO desktop_requests VALUES(?,?,?,?,?,?,?,NULL,1)', (rid,cid,fingerprint,'queued',source,int(spoken),token.trace_id))
                if identity:
                    self.db.execute('INSERT INTO desktop_model_turns VALUES(?,?)',(rid,json.dumps(identity)))
                    self.db.execute('INSERT INTO desktop_model_selection VALUES(?,?,?) ON CONFLICT(conversation_id) DO NOTHING',(cid,identity['model_id'],''))
                for role,content,status in [('user',text,'accepted'),('assistant','','queued')]:
                    self.db.execute('''INSERT INTO desktop_messages
                        (message_id,conversation_id,role,content,timestamp,status,request_id,task_id,source)
                        VALUES(?,?,?,?,?,?,?,?,?)''',(uuid.uuid4().hex,cid,role,content,now,status,rid,token.trace_id,source))
                self.db.execute('''UPDATE desktop_chats SET updated_at=?,title=CASE
                    WHEN renamed=0 AND (SELECT count(*) FROM desktop_messages WHERE conversation_id=? AND role='user')=1
                    THEN ? ELSE title END WHERE conversation_id=?''',(now,cid,' '.join(text.split())[:56],cid))
            self.tokens[rid] = token
            self.queue.put_nowait((rid,text,token,spoken,evidence))
            return self.request(rid)

    def append(self, rid, chunk):
        # Real callbacks only; bounded cadence is implemented by the client worker.
        with self.lock, self.db:
            self.db.execute("UPDATE desktop_messages SET content=content||?,version=version+1 WHERE request_id=? AND role='assistant'",(chunk,rid))
            self.db.execute('UPDATE desktop_requests SET revision=revision+1 WHERE request_id=?',(rid,))

    def event(self, rid, event):
        # Only a small safe task-state hint. Never publish thinking or arguments.
        if event['event'] in ('tool_start','tool_end'):
            token = self.tokens.get(rid)
            if token: token.desktop_tool = event.get('tool','') if event['event']=='tool_start' else ''
        if event['event']=='web_sources':
            token=self.tokens.get(rid)
            if token:self.save_sources(rid,token)
        if event['event'] in ('brain_request','brain_response'):
            token=self.tokens.get(rid)
            identity=getattr(token,'model_identity',None)
            if identity:
                if event['event']=='brain_request':
                    identity.update(requested_model=event.get('model'), options=event.get('options',identity['options']))
                elif event.get('done') and event.get('model'):
                    # Actual identity is evidence from the completed runtime
                    # response, never a copy of the requested selector label.
                    identity.update(actual_model=event['model'], response_completed=True)
                with self.lock,self.db:
                    self.db.execute('UPDATE desktop_model_turns SET identity=? WHERE request_id=?',(json.dumps(identity),rid))

    def save_sources(self,rid,token):
        with self.lock,self.db:
            if self.is_deleted(token.session_id):return
            self.db.execute('INSERT OR REPLACE INTO desktop_sources VALUES(?,?,?)',
                (rid,getattr(token,'web_status','local'),json.dumps(getattr(token,'web_sources',[]),ensure_ascii=False)))
            self.db.execute("UPDATE desktop_messages SET version=version+1 WHERE request_id=? AND role='assistant'",(rid,))

    def finish(self, rid, token):
        if hasattr(token,'web_status'):self.save_sources(rid,token)
        snapshot = getattr(token,'result_snapshot',{})
        status = snapshot.get('status','FAILED').lower()
        if status == 'cancelled': status = 'interrupted'
        if status not in ('completed','interrupted','failed','waiting','blocked','unverified'): status = 'failed'
        with self.lock, self.db:
            answer = getattr(token,'answer',None)
            if status == 'completed' and getattr(token,'model_identity',None):
                if token.model_identity.get('actual_model'):
                    self.db.execute('UPDATE desktop_model_selection SET last_good=? WHERE conversation_id=?',(token.model_identity['model_id'],token.session_id))
            if answer is not None and status != 'interrupted':
                self.db.execute("UPDATE desktop_messages SET content=? WHERE request_id=? AND role='assistant'",(answer,rid))
            self.db.execute("UPDATE desktop_messages SET status=?,version=version+1 WHERE request_id=? AND role='assistant'",(status,rid))
            self.db.execute("UPDATE desktop_messages SET status='completed',version=version+1 WHERE request_id=? AND role='user'",(rid,))
            # Keep exception detail out of UI/log snapshots: it may contain payloads.
            error = 'Request failed; inspect the local log by task ID' if getattr(token,'error',None) else None
            self.db.execute('UPDATE desktop_requests SET status=?,error=?,revision=revision+1 WHERE request_id=?',(status,error,rid))
            self.db.execute('UPDATE desktop_chats SET updated_at=? WHERE conversation_id=?',(utc_now(),token.session_id))
        self.tokens.pop(rid,None)

    def cancel(self, rid):
        with self.lock:
            token = self.tokens.get(rid)
            if token:
                token.cancel(reason='desktop_stop',source='desktop')
                speech = getattr(token,'speech_token',None)
                if speech: speech.cancel(reason='desktop_stop',source='desktop')
                with self.db:
                    self.db.execute("UPDATE desktop_requests SET status='cancelling',revision=revision+1 WHERE request_id=?",(rid,))
                    self.db.execute("UPDATE desktop_messages SET status='cancelling',version=version+1 WHERE request_id=? AND role='assistant'",(rid,))
            return self.request(rid)

    def _dispatch(self):
        while not self.closed.is_set():
            try: rid,text,token,spoken,evidence = self.queue.get(timeout=.1)
            except Empty: continue
            admitted = False
            while not self.closed.is_set() and not token.event.is_set():
                with self.loop.input_lock:
                    generation = self.loop.generation
                    busy = self.loop.agent.orchestrator.current_task or (generation and hasattr(generation,'trace_id') and not generation.completed.is_set())
                    if not busy and not self.loop.manual_recording and not self.loop.manual_pending:
                        with self.lock, self.db:
                            self.db.execute("UPDATE desktop_requests SET status='running',revision=revision+1 WHERE request_id=?",(rid,))
                            self.db.execute("UPDATE desktop_messages SET status='running',version=version+1 WHERE request_id=? AND role='assistant'",(rid,))
                        self.loop._enqueue(text,evidence=evidence,silent=not spoken,token=token)
                        admitted = True
                        break
                self.closed.wait(.08)
            if admitted:
                while not token.completed.wait(.1) and not self.closed.is_set(): pass
            else:
                token.cancel(reason='shutdown' if self.closed.is_set() else 'desktop_stop',source='desktop')
                self.loop._complete_turn(token)

    def handle(self, request):
        op = request['operation']
        cid = request.get('conversation_id','')
        if op == 'desktop.projects':return self.projects()
        if op == 'desktop.projectSave':return self.save_project(request.get('title'),request.get('project_id'),request.get('workspace_path'))
        if op == 'desktop.moveToProject':return self.move_to_project(cid,request.get('project_id'))
        if op == 'desktop.chats': return self.chats(request.get('offset',0),request.get('archived',False))
        if op == 'desktop.deleted':return [r[0] for r in self.db.execute('SELECT conversation_id FROM desktop_deleted')]
        if op == 'desktop.archive': return self.archive(cid,request.get('archived'))
        if op == 'desktop.pin': return self.pin(cid,request.get('pinned'))
        if op == 'desktop.delete':
            if self.is_deleted(cid):return dict(conversation_id=cid,deleted=True)
            return self.delete(cid)
        if op == 'desktop.create': return self.create()
        if op == 'desktop.rename': return self.rename(cid,request.get('title'))
        if op == 'desktop.history': return self.history(cid,request.get('before',0),after=request.get('after'))
        if op == 'desktop.submit': return self.submit(cid,request.get('client_request_id'),request.get('text'),spoken=bool(request.get('spoken',False)),model_id=request.get('model_id'))
        if op == 'desktop.request':
            result = self.request(request.get('client_request_id'))
            token = self.tokens.get(request.get('client_request_id'))
            result['tool'] = getattr(token,'desktop_tool','')
            return result
        if op == 'desktop.cancel': return self.cancel(request.get('client_request_id'))
        raise ValueError('Unknown desktop operation')

    def close(self):
        self.closed.set()
        for token in list(self.tokens.values()): token.cancel(reason='shutdown',source='runtime')
        self.thread.join(2)
