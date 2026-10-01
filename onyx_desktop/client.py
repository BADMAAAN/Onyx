from __future__ import annotations

import json
from pathlib import Path
from queue import Queue, Empty
from threading import Thread, Event
from time import monotonic
import subprocess
import uuid

from PySide6.QtCore import QObject, Property, Signal, Slot, QAbstractListModel, QModelIndex, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from .transport import LocalTransport
from .rendering import blocks
from .preferences import DEFAULTS, GROUPS, validate, validate_shortcut_conflict, atomic_save
from .presentation import public_status


class MessageModel(QAbstractListModel):
    fields = ('message_id','role','content','status','blocks','request_id','ordinal','provenance','sources','model_identity')
    def __init__(self):
        super().__init__()
        self.rows=[]
    def roleNames(self): return {Qt.UserRole+i+1:name.encode() for i,name in enumerate(self.fields)}
    def rowCount(self,parent=QModelIndex()): return len(self.rows)
    def data(self,index,role):
        if index.isValid() and role>Qt.UserRole:
            key=self.fields[role-Qt.UserRole-1]
            return self.rows[index.row()].get(key)
    def replace(self,rows):
        self.beginResetModel(); self.rows=rows; self.endResetModel()
    def reconcile(self,rows):
        # Stable delegates and heights for all unchanged historical messages.
        keys=[r['message_id'] for r in rows]
        for i in range(len(self.rows)-1,-1,-1):
            if self.rows[i]['message_id'] not in keys:
                self.beginRemoveRows(QModelIndex(),i,i); self.rows.pop(i); self.endRemoveRows()
        for i,row in enumerate(rows):
            if i>=len(self.rows) or self.rows[i]['message_id']!=row['message_id']:
                self.beginInsertRows(QModelIndex(),i,i); self.rows.insert(i,row); self.endInsertRows()
            elif self.rows[i]!=row:
                self.rows[i]=row; self.dataChanged.emit(self.index(i),self.index(i))


def prepare(rows):
    for row in rows:
        row['blocks'] = blocks(row['content']) if row['status'] not in ('running','queued','cancelling') else [dict(code=False,language='',text=row['content'])]
    return rows


class Worker(QObject):
    received=Signal(str,object)
    def __init__(self,root,prefs_path):
        super().__init__()
        self.root,self.path=Path(root),Path(prefs_path)
        self.transport=LocalTransport(root)
        self.commands,self.stop=Queue(),Event()
        self.selected=''; self.connected=False; self.launched=False;self.ever_connected=False
        self.prefs={}; self.pending={}; self.last_poll=0; self.last_chats=0;self.history_revision=None;self.block_cache={}
        self.archived=False
        self.dictation_session=None
        self.model_poll=0;self.model_refresh=0;self.has_models=False;self.capabilities=[]
        self.thread=Thread(target=self.run,name='OnyxDesktopIO',daemon=True)
    def save(self):
        atomic_save(self.path,self.prefs)
    def start_backend(self):
        # One launch per client lifetime. Backend's OS lock is the final arbiter.
        if self.launched: return
        self.launched=True
        launcher=self.root/'run.ps1'
        if not launcher.is_file(): raise RuntimeError('Backend launcher not found. Select its installation with --backend-root.')
        subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(launcher),'--background'],
                         cwd=self.root,creationflags=subprocess.CREATE_NO_WINDOW,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def run(self):
        try:self.received.emit('build',json.loads((Path(__file__).resolve().parent.parent/'manifest.json').read_text(encoding='utf-8')).get('build_id','dev'))
        except (OSError,ValueError):self.received.emit('build','dev')
        try: self.prefs=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError): self.prefs={}
        if not isinstance(self.prefs,dict):self.prefs={}
        for key,default in DEFAULTS.items():
            try:validate(key,self.prefs.get(key,default))
            except ValueError:self.prefs[key]=default
        self.pending=self.prefs.setdefault('pending',{})
        self.prefs.setdefault('newDraftId','draft:'+uuid.uuid4().hex)
        self.selected=self.prefs.get('selected','') if self.prefs.get('startup')=='last' else self.prefs['newDraftId']
        self.prefs['selected']=self.selected or self.prefs['newDraftId']
        self.selected=self.prefs['selected']
        from copy import deepcopy
        self.received.emit('prefs',deepcopy(self.prefs))
        started=monotonic(); next_connect=0
        while not self.stop.is_set() or not self.commands.empty():
            try:
                command,args=self.commands.get(timeout=.05)
                if command=='draft':
                    self.prefs.setdefault('drafts',{})[args['cid']]=args['text']; self.save()
                elif command=='prefs':
                    self.prefs.update(args); self.save()
                elif command=='preference':
                    key,value=args['key'],validate(args['key'],args['value'])
                    validate_shortcut_conflict(key,value,self.prefs)
                    candidate=dict(self.prefs,**{key:value})
                    try:
                        atomic_save(self.path,candidate)
                        self.prefs=candidate
                        self.received.emit('preferenceSaved',dict(key=key,value=value))
                    except OSError:
                        self.received.emit('error','Settings could not be saved; previous value retained.')
                elif command=='backendSettings':
                    self.received.emit('backendSettings',self.transport.call('settings.get'))
                    self.received.emit('memories',self.transport.call('memory.list'))
                elif command=='feature':
                    # Lengthy metadata/Git work cannot stall draft saving,
                    # history streaming or dictation polling.
                    def execute_feature(arguments):
                        op=arguments.pop('op')
                        try:
                            result=self.transport.call(op,**arguments)
                            self.received.emit('featureResult',dict(op=op,result=result))
                        except Exception as exc:self.received.emit('featureError',str(exc))
                    Thread(target=execute_feature,args=(dict(args),),name='OnyxFeatureIO',daemon=True).start()
                elif command=='chooseModel':
                    try:
                        result=self.transport.call('models.select',**args)
                        self.received.emit('modelSelected',result)
                    except Exception as exc:self.received.emit('modelSelectError',dict(conversation_id=args['conversation_id'],error=str(exc)))
                    self.model_poll=0
                elif command=='project':
                    op=args.pop('op');self.transport.call(op,**args)
                    self.received.emit('projects',self.transport.call('desktop.projects'));self.last_chats=0
                elif command=='memory':
                    op=args.pop('op');self.transport.call(op,**args)
                    self.received.emit('memories',self.transport.call('memory.list'))
                    self.received.emit('settingsError','Saved')
                elif command=='backendPreference':
                    try:
                        result=self.transport.call('settings.save',**args)
                        self.received.emit('backendSaved',result)
                        self.received.emit('backendSettings',self.transport.call('settings.get'))
                    except RuntimeError as exc:self.received.emit('settingsError',str(exc))
                elif command=='clearWebCache':
                    self.transport.call('settings.clearWebCache')
                    self.received.emit('settingsError','Web cache cleared')
                elif command=='export':
                    rows=[];before=0
                    while True:
                        page=self.transport.call('desktop.history',conversation_id=args['cid'],before=before)
                        rows=page['messages']+rows
                        if not page['has_more']:break
                        before=page['messages'][0]['ordinal']
                    path=self.path.parent/'exports'/(args['cid'].replace(':','-')+'.json')
                    atomic_save(path,dict(conversation_id=args['cid'],messages=rows))
                    self.received.emit('exported',str(path))
                elif command=='select':
                    self.selected=args['cid']; self.history_revision=None; self.prefs['selected']=self.selected; self.save(); self.last_poll=0
                elif command=='retry':
                    self.connected=False; next_connect=0;self.launched=False;self.ever_connected=False;started=monotonic()
                elif command=='submit':
                    rid=args['client_request_id']
                    draft_text=args.pop('draft_text',args['text']);files=args.pop('draft_files',[])
                    self.pending[rid]=dict(cid=args['conversation_id'],text=draft_text,files=files)
                    self.save()  # A crash/ambiguous HTTP result cannot cause a replay.
                    try:
                        result=self.transport.call('desktop.submit',**args)
                        self.accept(rid,result)
                    except Exception:
                        self.connected=False
                        self.received.emit('connection',dict(ready=False,label='Connection lost — checking request ID'))
                elif command=='create':
                    result=self.transport.call('desktop.create'); self.received.emit('created',result)
                    self.last_chats=0
                elif command=='rename':
                    self.transport.call('desktop.rename',**args); self.last_chats=0
                elif command=='archive':
                    self.transport.call('desktop.archive',**args);self.last_chats=0
                elif command=='pin':
                    self.transport.call('desktop.pin',**args);self.last_chats=0
                elif command=='delete':
                    result=self.transport.call('desktop.delete',**args)
                    cid=args['conversation_id']
                    for field in ('drafts','attachments'):
                        self.prefs.setdefault(field,{}).pop(cid,None)
                    self.pending={rid:v for rid,v in self.pending.items() if v['cid']!=cid}
                    self.prefs['pending']=self.pending
                    self.save();self.received.emit('deleted',result);self.last_chats=0
                elif command=='dictation':
                    op=args.pop('op')
                    try:
                        result=self.transport.call(op,**args)
                        self.dictation_session=result['session_id'] if result['state'] in ('recording','transcribing') else None
                        self.received.emit('dictation',result)
                    except Exception:
                        # Query by the same identity after reconnect, never replay start.
                        self.dictation_session=args['session_id']
                        self.received.emit('error','Dictation connection failed; checking session. Draft preserved.')
                elif command=='showArchive':
                    self.archived=args['archived'];self.last_chats=0
                elif command=='attach':
                    from .attachments import read_attachment
                    try:self.received.emit('attachment',dict(cid=args['cid'],file=read_attachment(args['path'])))
                    except (ValueError,OSError) as exc:self.received.emit('error',str(exc))
                elif command=='older':
                    data=self.transport.call('desktop.history',**args); prepare(data['messages']);self.received.emit('older',data)
                elif command=='cancel':
                    self.transport.call('desktop.cancel',**args);self.last_poll=0
                elif command=='voice':
                    op=args.pop('op')
                    try:
                        result=self.transport.call(op,**args);self.last_poll=0
                        if op=='voice.live' and result.get('started'):self.received.emit('live',result)
                        elif op=='voice.liveBackground' and result.get('background'):self.received.emit('liveBackground',result)
                    except Exception as exc:self.received.emit('liveError' if op in ('voice.live','voice.liveBackground') else 'error',str(exc))
            except Empty: pass
            except Exception as exc:
                self.received.emit('error',type(exc).__name__+': operation failed; your draft is preserved')
                self.connected=False
            now=monotonic()
            if not self.stop.is_set() and not self.connected and now>=next_connect:
                next_connect=now+2
                try:
                    hello=self.transport.connect();self.connected=True;self.ever_connected=True;self.launched=True
                    self.received.emit('hello',hello)
                    self.has_models='models' in hello.get('capabilities',[])
                    self.capabilities=hello.get('capabilities',[])
                    if 'chat_delete' in hello.get('capabilities',[]):
                        deleted=set(self.transport.call('desktop.deleted'))
                        for field in ('drafts','attachments'):
                            self.prefs[field]={k:v for k,v in self.prefs.get(field,{}).items() if k not in deleted}
                        self.pending={k:v for k,v in self.pending.items() if v['cid'] not in deleted}
                        self.prefs['pending']=self.pending;self.save()
                        for cid in deleted:self.received.emit('deleted',dict(conversation_id=cid))
                    # Resolve uncertain submissions by ID; never automatically resubmit.
                    for rid in list(self.pending): self.accept(rid,self.transport.call('desktop.request',client_request_id=rid))
                    self.last_chats=self.last_poll=0;self.history_revision=None
                except FileNotFoundError:
                    try: self.start_backend()
                    except Exception as exc: self.received.emit('error',str(exc))
                    self.received.emit('connection',dict(ready=False,label='Backend stopped — Retry to start Onyx' if self.ever_connected else 'Starting backend…' if now-started<45 else 'Backend did not connect. Retry after checking Onyx.'))
                except Exception as exc:
                    self.received.emit('connection',dict(ready=False,label=str(exc) if isinstance(exc,RuntimeError) else 'Backend unavailable — reconnecting'))
            if not self.stop.is_set() and self.connected and now-self.last_poll>.35:
                self.last_poll=now
                try:
                    self.received.emit('hello',self.transport.call('hello'))
                    if self.dictation_session:
                        result=self.transport.call('dictation.status',session_id=self.dictation_session)
                        self.received.emit('dictation',result)
                        if result['state'] not in ('recording','transcribing'):self.dictation_session=None
                    if now-self.last_chats>2:
                        self.received.emit('chats',self.transport.call('desktop.chats',archived=self.archived));self.last_chats=now
                        if 'projects' in self.capabilities:self.received.emit('projects',self.transport.call('desktop.projects'))
                    if self.has_models and now-self.model_poll>2:
                        self.model_poll=now
                        if now-self.model_refresh>30:
                            self.transport.call('models.refresh',conversation_id=self.selected);self.model_refresh=now
                        self.received.emit('models',self.transport.call('models.list',conversation_id=self.selected))
                    if self.selected:
                        data=self.transport.call('desktop.history',conversation_id=self.selected,after=self.history_revision)
                        if not data.get('unchanged'):
                            self.history_revision=data.get('revision')
                            for row in data['messages']:
                                key=(row['message_id'],row['version'])
                                if key not in self.block_cache:self.block_cache[key]=prepare([row])[0]['blocks']
                                row['blocks']=self.block_cache[key]
                            if len(self.block_cache)>200:self.block_cache={}
                            self.received.emit('history',data)
                except Exception:
                    self.connected=False
                    self.received.emit('connection',dict(ready=False,label='Connection lost — reconnecting'))
        self.save()
    def accept(self,rid,result):
        pending=self.pending.get(rid)
        if result.get('status')!='not_found' and pending and pending['cid']==self.prefs.get('newDraftId'):
            self.prefs['newDraftId']='draft:'+uuid.uuid4().hex
        self.pending.pop(rid,None); self.save()
        self.received.emit('accepted',dict(rid=rid,result=result))
        self.last_poll=self.last_chats=0


class Client(QObject):
    changed=Signal()
    sidebarChanged=Signal()
    selectionChanged=Signal()
    languageChanged=Signal()
    modelsChanged=Signal()
    modelStateChanged=Signal()
    currentModelChanged=Signal()
    appsChanged=Signal()
    memoriesChanged=Signal()
    backendSettingsChanged=Signal()
    voiceEnvelopeChanged=Signal()
    draftChanged=Signal()
    contentChanged=Signal()
    liveStarted=Signal()
    liveBackgroundStarted=Signal()
    insertTranscript=Signal(int,str)
    def __init__(self,root,prefs,preview=False):
        super().__init__()
        from .preferences import system_font
        self._systemFont=system_font()
        self.messages=MessageModel()
        self._chats=[]; self._selected=''; self._drafts={}; self._prefs={}
        self._ready=False;self._connection='Connecting…';self._error='';self._voice='unavailable';self._voiceReason='Connecting to Onyx';self._hasMore=False
        self._active=[];self._pending={};self._older=[];self._historyLoaded=False;self._seenFailures=set()
        self._archived=False;self._attachments={};self._voiceHotkey='';self._capabilities=[]
        self._preview=preview
        self._newDraftId='draft:'+uuid.uuid4().hex
        self._settingsStatus=''
        self._backendSettings={}
        self._modelState={};self._modelChoices={};self._apps=[];self._gitState={};self._review={};self._importReview={};self._featureStatus='';self._internet='off'
        self._voiceEnvelope=0.;self._voiceNotice=''
        self._projects=[];self._modelPending=set();self._modelRevisions={};self._build='dev';self._lastHello=None;self._liveState='IDLE'
        self._savedMemories=[]
        self._dictation={};self._dictationState='editing';self._draftRevisions={};self._seenDictations=set();self._liveActive=False
        self._liveStarting=False;self._liveBackgroundPending=False;self._liveWanted=False
        self._root=Path(root);self._prefsPath=Path(prefs)
        if preview:
            self._selected='preview-0';self._historyLoaded=True;self._ready=True
            self._voice='listening';self._voiceReason='';self._connection='Visual preview — no backend'
            self._prefs={'language':'en','reducedMotion':True}
            self._chats=[]
        else:
            self.worker=Worker(root,prefs);self.worker.received.connect(self.receive)
            self.worker.thread.start()
    def command(self,name,**args):
        if not self._preview:self.worker.commands.put((name,args))
    @Property('QVariantMap',notify=modelStateChanged)
    def modelState(self):return self._modelState
    @Property('QVariantMap',notify=changed)
    def importReview(self):return self._importReview
    @Slot(QUrl)
    def inspectImport(self,url):
        self._importReview={};self.feature('models.inspect',{'path':url.toLocalFile()})
    @Property('QVariantList',notify=modelsChanged)
    def models(self):return self._modelState.get('rows',[])
    @Property(str,notify=currentModelChanged)
    def currentModel(self):return self._modelChoices.get(self.selected,'')
    @Property(str,notify=changed)
    def internetMode(self):return self._internet
    @Property(float,notify=voiceEnvelopeChanged)
    def voiceEnvelope(self):return self._voiceEnvelope
    @Property(bool,notify=changed)
    def modelChanging(self):return self.selected in self._modelPending
    @Property('QVariantList',notify=sidebarChanged)
    def projects(self):return self._projects
    @Property('QVariantMap',notify=sidebarChanged)
    def sidebarCollapsed(self):return self._prefs.get('sidebarCollapsed',{})
    @Slot(str)
    def toggleSection(self,key):
        current=dict(self.sidebarCollapsed);current[key]=not current.get(key,False);self.preference('sidebarCollapsed',current)
    @Slot(str,str,str)
    def saveProject(self,title,project_id,path):self.command('project',op='desktop.projectSave',title=title,project_id=project_id or None,workspace_path=path or None)
    @Slot(str,str)
    def moveChat(self,cid,project_id):self.command('project',op='desktop.moveToProject',conversation_id=cid,project_id=project_id or None)
    @Property('QVariantList',notify=appsChanged)
    def apps(self):return self._apps
    @Property('QVariantMap',notify=changed)
    def gitState(self):return self._gitState
    @Property('QVariantMap',notify=changed)
    def gitReview(self):return self._review
    @Property(str,notify=changed)
    def featureStatus(self):return public_status(self._featureStatus)
    @Property('QVariantMap',notify=changed)
    def appearance(self):
        values={k:self._prefs.get(k,DEFAULTS.get(k)) for k in ('backgroundColor','textColor','accentColor','fontFamily','density','backgroundPreset','livePalette','liveSize','liveSpeed','liveBackground','sidebarOpen','settingsExpanded')}
        if values['fontFamily'] in (None,'Segoe UI'):values['fontFamily']=self._systemFont
        return values
    @Property(str,constant=True)
    def systemFont(self):return self._systemFont
    @Slot(str,'QVariantMap')
    def feature(self,op,args):
        self._featureStatus='Working…';self.changed.emit()
        self.command('feature',op=op,**dict(args))
    @Slot()
    def refreshModels(self):self.feature('models.refresh',dict(conversation_id=self.selected))
    @Slot(str)
    def selectModel(self,name):
        if self.modelChanging or not self.ready:return
        self._modelPending.add(self.selected);self._featureStatus='';self.changed.emit()
        self.command('chooseModel',conversation_id=self.selected,model=name)
    @Slot(QUrl)
    def chooseProject(self,url):self.feature('git.select',dict(path=url.toLocalFile()))
    @Slot()
    def dismissReview(self):self._review={};self.changed.emit()
    @Slot()
    def openLicenses(self):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(__file__).parent/'NOTICES.md')))
    @Slot(str,str,QUrl,int)
    def installModel(self,kind,name,url,limitMiB):
        self.feature('models.install',dict(kind=kind,model=name,path=url.toLocalFile(),limit=limitMiB*1024**2))
    @Slot(str,bool)
    def expandSettings(self,key,expanded):
        current=dict(self.appearance['settingsExpanded'] or {});current[key]=expanded;self.preference('settingsExpanded',current)
    @Property(bool,constant=True)
    def preview(self):return self._preview
    @Property(bool,notify=sidebarChanged)
    def archived(self):return self._archived
    @Property('QVariantList',notify=changed)
    def attachments(self):return self._attachments.get(self.selected,[])
    @Property(str,notify=changed)
    def voiceHotkey(self):return self._voiceHotkey
    @Slot(bool)
    def showArchive(self,value):
        if value==self._archived:return
        self._archived=value;self.command('showArchive',archived=value);self.sidebarChanged.emit();self.changed.emit()
    @Slot(bool)
    def archiveSelected(self,value):
        if self.selected:self.command('archive',conversation_id=self.selected,archived=value)
    @Slot(QUrl)
    def attachFile(self,url):
        if url.isLocalFile():self.command('attach',cid=self.selected,path=url.toLocalFile())
    def saveAttachments(self):
        self.command('prefs',attachments=self._attachments);self.changed.emit()
    @Slot(int)
    def removeAttachment(self,index):
        if 0<=index<len(self.attachments):self._attachments[self.selected].pop(index);self.saveAttachments()
    @Slot()
    def startLive(self):
        if self.preview or self._liveStarting or self.liveActive:return
        if 'voice_live' not in self._capabilities:
            self._error='Live requires the updated backend; reopen Onyx after its orderly restart.';self.changed.emit();return
        if self.ready and self._voice=='listening' and not self.dictationBusy:
            self._liveStarting=True;self._liveWanted=True
            self.command('voice',op='voice.live',conversation_id=self.selected)
    @Slot()
    def stopLive(self):
        self._liveWanted=False;self._liveStarting=False;self._liveBackgroundPending=False
        self.command('voice',op='voice.stopLive')
    @Slot()
    def backgroundLive(self):
        if self.preview or not self.ready or not self.liveActive or self._liveBackgroundPending:return
        if 'voice_live_background' not in self._capabilities:
            self._error='Background Live requires the updated Onyx backend.';self.changed.emit();return
        self._liveWanted=True;self._liveBackgroundPending=True
        self.command('voice',op='voice.liveBackground',conversation_id=self.selected)
    @Property(bool,notify=changed)
    def liveActive(self):return self._liveActive
    @Property(str,notify=changed)
    def liveState(self):return self._liveState
    @Property(str,notify=changed)
    def voiceNotice(self):return self._voiceNotice
    @Property(str,notify=changed)
    def dictationState(self):return self._dictationState
    @Property(bool,notify=changed)
    def dictationBusy(self):return self._dictationState in ('starting','recording','transcribing')
    @Slot(int)
    def startDictation(self,position):
        if not self.ready or self.dictationBusy or self.liveActive:return
        if 'dictation' not in self._capabilities:
            self._error='Dictation requires the updated backend.';self.changed.emit();return
        self._dictation=dict(session_id=uuid.uuid4().hex,purpose='dictation',conversation_id=self.selected,
            revision=self._draftRevisions.get(self.selected,0),position=max(0,min(position,len(self.draft))))
        self._dictationState='starting';self.changed.emit()
        self.command('dictation',op='dictation.start',**self._dictation)
    @Slot()
    def finishDictation(self):
        if self._dictationState=='recording':
            self._dictationState='transcribing';self.changed.emit()
            self.command('dictation',op='dictation.commit',session_id=self._dictation['session_id'])
    @Slot()
    def cancelDictation(self):
        if self._dictation:
            self._seenDictations.add(self._dictation['session_id'])
            self.command('dictation',op='dictation.cancel',session_id=self._dictation['session_id'])
            self._dictationState='editing';self.changed.emit()
    @Slot(str,str)
    def renameChat(self,cid,title):self.command('rename',conversation_id=cid,title=title)
    @Slot(str,bool)
    def archiveChat(self,cid,value):self.command('archive',conversation_id=cid,archived=value)
    @Slot(str,bool)
    def pinChat(self,cid,value):self.command('pin',conversation_id=cid,pinned=value)
    @Slot(str)
    def deleteChat(self,cid):
        if self._dictation.get('conversation_id')==cid:self.cancelDictation()
        self.command('delete',conversation_id=cid)
    @Property(QObject,constant=True)
    def messageModel(self):return self.messages
    @Property('QVariantList',notify=sidebarChanged)
    def chats(self):return self._chats
    @Property(str,notify=selectionChanged)
    def selected(self):return self._selected
    @Property(str,notify=changed)
    def title(self):return next((r['title'] for r in self._chats if r['conversation_id']==self._selected),'New chat')
    @Property(bool,notify=changed)
    def ready(self):return self._ready
    @Property(str,notify=changed)
    def connection(self):return self._connection
    @Property(str,notify=changed)
    def error(self):return self._error
    @Property(str,notify=changed)
    def voiceState(self):return self._voice
    @Property(str,notify=changed)
    def voiceReason(self):return self._voiceReason
    @Property(bool,notify=changed)
    def welcome(self):return (not self.selected or self._historyLoaded) and not any(r.get('status')!='sending' for r in self.messages.rows)
    @Property(bool,notify=changed)
    def busy(self):return bool(self._active or any(v['cid']==self._selected for v in self._pending.values()))
    @Property(bool,notify=changed)
    def hasMore(self):return self._hasMore
    @Property(str,notify=draftChanged)
    def draft(self):return self._drafts.get(self._selected,'')
    @Property(bool,notify=changed)
    def reducedMotion(self):return bool(self._prefs.get('reducedMotion',False))
    @Property(bool,notify=changed)
    def spoken(self):return bool(self._prefs.get('spoken',False))
    @Property(str,notify=languageChanged)
    def language(self):return self._prefs.get('language','en')
    @Property(int,notify=changed)
    def textSize(self):return int(self._prefs.get('textSize',15))
    @Property(float,notify=changed)
    def glow(self):return float(self._prefs.get('glow',0.5))
    @Property(bool,notify=changed)
    def showWelcome(self):return self._prefs.get('showWelcome',True)
    @Property(str,notify=changed)
    def startup(self):return self._prefs.get('startup','welcome')
    @Property(str,notify=changed)
    def closeBehavior(self):return self._prefs.get('closeBehavior','hide')
    @Slot(int,int,int,int)
    def saveGeometry(self,x,y,width,height):
        if not self.preview:self.command('prefs',geometry=[x,y,width,height])
    @Property(str,notify=changed)
    def settingsStatus(self):return public_status(self._settingsStatus)
    @Property('QVariantMap',notify=changed)
    def shortcuts(self):return {k:self._prefs.get(k,v) for k,v in DEFAULTS.items() if k.startswith('shortcut')}
    @Slot(str)
    def resetShortcut(self,key):
        if key.startswith('shortcut') and key in DEFAULTS:self.preference(key,DEFAULTS[key])
    @Property(str,notify=changed)
    def diagnostics(self):
        details=[self._voiceReason,self._connection,self._error,self._featureStatus,self._settingsStatus,self._modelState.get('error','')]
        details.extend(job.get('error','') for job in self._modelState.get('jobs',[]))
        return 'Onyx 0.5.0 · '+self._build+'\n'+str(self._prefsPath.parent)+'\nBackend: '+str(self._root)+'\nHistory: '+str(self._root/'data/kk.db')+'\n'+'\n'.join(str(value) for value in details if value)
    @Property('QVariantMap',notify=backendSettingsChanged)
    def backendSettings(self):return self._backendSettings
    @Property('QVariantList',notify=memoriesChanged)
    def savedMemories(self):return self._savedMemories
    @Slot(str,str)
    def saveMemory(self,key,value):self.command('memory',op='memory.save',key=key,value=value)
    @Slot(str)
    def forgetMemory(self,key):self.command('memory',op='memory.forget',key=key)
    @Slot()
    def loadSettings(self):
        if self.ready:self.command('backendSettings')
    @Slot()
    def clearWebCache(self):
        if self.ready:self.command('clearWebCache')
    @Slot(str,'QVariant')
    def backendPreference(self,key,value):
        if self.ready:self._settingsStatus='Saving…';self.command('backendPreference',key=key,value=value);self.changed.emit()
    @Slot()
    def exportChat(self):
        if self.ready and self.messages.rows:self.command('export',cid=self.selected)
    @Slot(str)
    def openSource(self,url):
        from urllib.parse import urlsplit
        from PySide6.QtGui import QDesktopServices
        known={s['url'] for r in self.messages.rows for s in r.get('sources',[])}
        known.update(a['docs'] for a in self._apps)
        if self.internetMode!='off' and url in known and urlsplit(url).scheme in ('http','https'):QDesktopServices.openUrl(QUrl(url))
    @Slot()
    def openLogs(self):
        from PySide6.QtGui import QDesktopServices
        if not self.preview:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root/'logs')))
    @Slot()
    def openData(self):
        from PySide6.QtGui import QDesktopServices
        if not self.preview:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root/'data')))
    @Slot(str)
    def resetGroup(self,group):
        for key in GROUPS.get(group,()):self.preference(key,DEFAULTS[key])
    @Slot(str,result=str)
    def tr(self,text):
        from .strings import RU
        return RU.get(text,text) if self.language=='ru' else text
    @Slot(str)
    def setDraft(self,text):
        if text==self._drafts.get(self._selected,''):return
        self._draftRevisions[self._selected]=self._draftRevisions.get(self._selected,0)+1
        self._drafts[self._selected]=text
        self.command('draft',cid=self._selected,text=text)
    @Slot(str,'QVariant')
    def preference(self,key,value):
        try:
            value=validate(key,value)
            validate_shortcut_conflict(key,value,self._prefs,self._backendSettings.get('show_hotkey',''))
        except ValueError as exc:self._settingsStatus=str(exc);self.changed.emit();return
        if self.preview:
            self._prefs[key]=value
            if key=='sidebarCollapsed':self.sidebarChanged.emit()
            if key=='language':self.languageChanged.emit()
        else:self.command('preference',key=key,value=value)
        self._settingsStatus='Saving…';self.changed.emit()
    @Slot()
    def newChat(self):
        if self.archived:self.showArchive(False)
        self.select(self._newDraftId)
        self._historyLoaded=True;self.changed.emit()
    @Slot(str)
    def select(self,cid):
        if cid==self._selected:return
        self._selected=cid;self._older=[];self._active=[];self._historyLoaded=False;self.messages.replace([])
        self.command('select',cid=cid);self.draftChanged.emit();self.selectionChanged.emit();self.currentModelChanged.emit();self.changed.emit()
        if not self.preview:self.feature('models.list',dict(conversation_id=cid))
    @Slot(str)
    def rename(self,title):self.command('rename',conversation_id=self.selected,title=title)
    @Slot()
    def send(self):
        text=self.draft
        if self.preview or not text.strip() or not self.ready or not self.selected or self.busy or self.dictationBusy or self.modelChanging or self.liveActive or self._liveStarting:return
        from .attachments import compose
        try:payload=compose(text,self.attachments)
        except ValueError as exc:self._error=str(exc);self.changed.emit();return
        from copy import deepcopy
        files=deepcopy(self.attachments)
        rid=uuid.uuid4().hex;self._pending[rid]=dict(cid=self.selected,text=text,files=files)
        rows=list(self.messages.rows)+[dict(message_id='pending:'+rid,role='user',content=text,status='sending',blocks=blocks(text),request_id=rid,ordinal=0)]
        self.messages.reconcile(rows);self._error='';self.changed.emit();self.contentChanged.emit()
        self.command('submit',conversation_id=self.selected,client_request_id=rid,text=payload,spoken=self.spoken,draft_text=text,draft_files=files,model_id=self.currentModel or None)
    @Slot()
    def cancel(self):
        request=next((r for r in self._active if r['status']=='running'),self._active[0] if self._active else None)
        if request:self.command('cancel',client_request_id=request['request_id'])
        else:
            rid=next((key for key,value in self._pending.items() if value['cid']==self.selected),None)
            if rid:self.command('cancel',client_request_id=rid)
    @Slot(str)
    def voice(self,action):
        if self.ready:self.command('voice',op='voice.'+action,conversation_id=self.selected)
    @Slot()
    def retry(self):self.command('retry')
    @Slot()
    def older(self):
        if self.messages.rows:self.command('older',conversation_id=self.selected,before=self.messages.rows[0].get('ordinal',0))
    @Slot(str)
    def copy(self,text):QGuiApplication.clipboard().setText(text)
    @Slot(str,object)
    def receive(self,kind,data):
        if kind=='build':self._build=data
        elif kind=='projects':
            if data==self._projects:return
            self._projects=data;self.sidebarChanged.emit()
        elif kind=='modelSelected':
            self._modelPending.discard(data['conversation_id']);self.receive('models',data)
        elif kind=='modelSelectError':
            self._modelPending.discard(data['conversation_id']);self._featureStatus=data['error'];self._error=data['error']
        elif kind=='featureError':self._featureStatus=data
        elif kind=='featureResult':
            op,result=data['op'],data['result'];self._featureStatus='Saved'
            if op=='models.inspect':self._importReview=result
            elif op.startswith('models.'):
                if 'rows' in result:self.receive('models',result)
                else:self.refreshModels()
            elif op=='apps.list':
                if self._apps!=result:self._apps=result;self.appsChanged.emit()
            elif op in ('git.select','git.status'):self._gitState=result
            elif op in ('git.preview','git.pushPreview'):self._review=dict(result,kind='push' if op=='git.pushPreview' else 'commit')
            elif op in ('git.commit','git.push'):
                self._review={};self._featureStatus=result.get('output','Completed');self.feature('git.status',{})
        elif kind=='models':
            cid=data['conversation_id'];revision=data.get('selection_revision',0)
            if revision<self._modelRevisions.get(cid,0):return
            self._modelRevisions[cid]=revision
            if self._modelState==data and self._modelChoices.get(cid)==data.get('model_id',''):return
            previous_model=self.currentModel
            self._modelChoices[data['conversation_id']]=data.get('model_id','')
            if data['conversation_id']==self.selected:
                different_rows=self._modelState.get('rows',[])!=data.get('rows',[])
                self._modelState=data;self.modelStateChanged.emit()
                if different_rows:self.modelsChanged.emit()
            if self.currentModel!=previous_model:self.currentModelChanged.emit()
        elif kind=='prefs':
            self._prefs=dict(DEFAULTS,fontFamily=self._systemFont);self._prefs.update(data)
            self._drafts=data.get('drafts',{});self._pending=data.get('pending',{}).copy()
            self._newDraftId=data.get('newDraftId',self._newDraftId)
            self._attachments=data.get('attachments',{})
            self._selected=data.get('selected','');self.draftChanged.emit()
            self._historyLoaded=self._selected==self._newDraftId
            self.sidebarChanged.emit();self.selectionChanged.emit();self.currentModelChanged.emit();self.languageChanged.emit()
        elif kind=='preferenceSaved':
            self._prefs[data['key']]=data['value'];self._settingsStatus='Saved'
            if data['key']=='sidebarCollapsed':self.sidebarChanged.emit()
            if data['key']=='language':self.languageChanged.emit()
        elif kind=='backendSettings':
            if self._backendSettings==data:return
            self._backendSettings=data;self.backendSettingsChanged.emit()
        elif kind=='memories':
            if self._savedMemories==data:return
            self._savedMemories=data;self.memoriesChanged.emit()
        elif kind=='settingsError':self._settingsStatus=data
        elif kind=='backendSaved':self._settingsStatus='Saved — backend restart required' if data['restart_required'] else 'Saved'
        elif kind=='exported':self._settingsStatus='Export: '+data
        elif kind=='connection':
            if (self._ready,self._connection)==(data['ready'],data['label']):return
            self._ready=data['ready'];self._connection=data['label'];self._lastHello=None
        elif kind=='hello':
            envelope=max(0.,min(1.,float(data.get('voice_envelope',0))))
            if envelope!=self._voiceEnvelope:self._voiceEnvelope=envelope;self.voiceEnvelopeChanged.emit()
            meaningful={k:data.get(k) for k in ('internet','text_ready','voice_state','voice_reason','voice_hotkey','capabilities','live_active','state','voice_notice')}
            if meaningful==self._lastHello:return
            self._lastHello=meaningful
            self._internet=data.get('internet','off')
            self._voiceEnvelope=max(0.,min(1.,float(data.get('voice_envelope',0))))
            self._ready=data['text_ready'];self._connection='Connected to Onyx' if self._ready else 'Loading…'
            self._voice=data['voice_state'];self._voiceReason=data['voice_reason']
            self._voiceHotkey=data.get('voice_hotkey','');self._capabilities=data.get('capabilities',[])
            self._liveActive=data.get('live_active',False)
            self._liveState=data.get('state','IDLE')
            self._voiceNotice=self.tr(data.get('voice_notice',''))
        elif kind=='dictation':
            sid=data['session_id']
            if sid!=self._dictation.get('session_id') or sid in self._seenDictations:return
            self._dictationState=data['state']
            if data['state'] in ('ready','error','cancelled'):
                self._seenDictations.add(sid)
                if data['state']=='error':self._error=data.get('error','Speech recognition failed; draft preserved')
                if data['state']=='ready':
                    cid=self._dictation['conversation_id'];current=self._drafts.get(cid,'')
                    position=self._dictation['position'] if self._draftRevisions.get(cid,0)==self._dictation['revision'] else len(current)
                    transcript=data['text']
                    if position and not current[position-1].isspace():transcript=' '+transcript
                    if position<len(current) and not current[position].isspace():transcript+=' '
                    if cid==self.selected:
                        # TextArea.insert preserves Qt's normal undo stack.
                        self.insertTranscript.emit(position,transcript)
                    else:
                        self._drafts[cid]=current[:position]+transcript+current[position:]
                        self._draftRevisions[cid]=self._draftRevisions.get(cid,0)+1
                        self.command('draft',cid=cid,text=self._drafts[cid])
        elif kind=='deleted':
            cid=data['conversation_id'];self._drafts.pop(cid,None);self._attachments.pop(cid,None)
            self._pending={rid:v for rid,v in self._pending.items() if v['cid']!=cid}
            self._chats=[c for c in self._chats if c['conversation_id']!=cid]
            self.sidebarChanged.emit()
            if self.selected==cid:
                self._newDraftId='draft:'+uuid.uuid4().hex;self.command('prefs',newDraftId=self._newDraftId);self.newChat()
        elif kind=='live':
            self._liveStarting=False
            if self._liveWanted and data.get('conversation_id')==self.selected:self.liveStarted.emit()
        elif kind=='liveBackground':
            self._liveBackgroundPending=False
            if self._liveWanted and data.get('conversation_id')==self.selected:self.liveBackgroundStarted.emit()
        elif kind=='liveError':
            self._liveStarting=False;self._liveBackgroundPending=False;self._error=data
        elif kind=='attachment':
            current=self._attachments.setdefault(data['cid'],[])
            if len(current)>=3 or sum(len(f['content']) for f in current)+len(data['file']['content'])>10000:
                self._error='Attachments are limited to three files and 10,000 characters per draft.'
            else:current.append(data['file']);self.saveAttachments()
        elif kind=='error':self._error=data
        elif kind=='chats':
            if data==self._chats:return
            self._chats=data;self.sidebarChanged.emit()
        elif kind=='created':self.select(data['conversation_id'])
        elif kind=='accepted':
            old=self._pending.pop(data['rid'],None)
            if old and data['result']['status']!='not_found' and old['cid']==self._newDraftId:
                self._newDraftId='draft:'+uuid.uuid4().hex
                self.command('prefs',newDraftId=self._newDraftId)
            if data['result']['status']=='not_found':self._error='Request was not accepted. Draft preserved; send again when ready.'
            elif old and self._drafts.get(old['cid'])==old['text']:
                self._drafts[old['cid']]='';self.command('draft',cid=old['cid'],text='');self.draftChanged.emit()
                if self._attachments.get(old['cid'],[])==old.get('files',[]):
                    self._attachments[old['cid']]=[];self.saveAttachments()
        elif kind in ('history','older') and data['conversation_id']==self.selected:
            self._historyLoaded=True
            if kind=='older':
                known={r['message_id'] for r in self._older}
                self._older=[r for r in data['messages'] if r['message_id'] not in known]+self._older
            canonical=data['messages'] if kind=='history' else [r for r in self.messages.rows if r['message_id'] not in {m['message_id'] for m in self._older}]
            ids={r['message_id'] for r in canonical}
            rows=[r for r in self._older if r['message_id'] not in ids]+canonical
            canonical_rids={r.get('request_id') for r in rows}
            rows += [r for r in self.messages.rows if r['message_id'].startswith('pending:') and r['request_id'] in self._pending and r['request_id'] not in canonical_rids]
            for row in rows:
                if row['role']=='assistant' and row['status'] in ('failed','interrupted') and row['request_id'] not in self._seenFailures:
                    self._seenFailures.add(row['request_id'])
                    original=next((r['content'] for r in rows if r['role']=='user' and r['request_id']==row['request_id']),'')
                    if not self.draft:
                        self.setDraft(original);self.draftChanged.emit()
            different=rows!=self.messages.rows
            self.messages.reconcile(rows)
            self._hasMore=data['has_more'];self._active=data['requests']
            if different:self.contentChanged.emit()
        self.changed.emit()
    def close(self):
        if self._preview:return
        self.worker.stop.set();self.worker.thread.join(4)
