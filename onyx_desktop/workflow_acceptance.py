"""Opt-in neutral integration checks of our own client, including confirmed Live.

Uses public client slots and the same reopen invocation as the existing tray.
It neither opens an audio device nor controls external application UI.
"""
import json
from pathlib import Path
from time import monotonic
from PySide6.QtCore import QTimer,QUrl
from PySide6.QtGui import QGuiApplication


class WorkflowAcceptance:
    def __init__(self,client,window,directory):
        self.client,self.window=client,window
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.started=monotonic();self.stage=0;self.checks={};self.cid='';self.ack=False;self.reopener=None
        self.file=self.directory/'Пример notes.txt'
        self.file.write_text('Calibration marker: quartz-731. Neutral local acceptance data.\n',encoding='utf-8')
        client.liveStarted.connect(self.on_live)
        self.timer=QTimer();self.timer.setInterval(100);self.timer.timeout.connect(self.step);self.timer.start()
    def on_live(self):self.ack=True
    def finish(self,error=None):
        if error:self.checks['error']=str(error)
        self.checks['original_file_retained']=self.file.is_file()
        self.timer.stop()
        (self.directory/'workflow.json').write_text(json.dumps(dict(checks=self.checks,conversation_id=self.cid,
            stage=self.stage,seconds=monotonic()-self.started),indent=2),encoding='utf-8')
        QGuiApplication.quit()
    def step(self):
        c=self.client
        try:
            if monotonic()-self.started>120:raise TimeoutError('Workflow stage '+str(self.stage)+'; '+c.error)
            if self.stage==0 and c.ready and c._historyLoaded:
                self.identity=dict(c.worker.transport.metadata);c.newChat();self.stage=1
            elif self.stage==1 and c.welcome and c._historyLoaded:
                self.cid=c.selected
                c.attachFile(QUrl.fromLocalFile(str(self.file.resolve())));self.stage=2
            elif self.stage==2 and len(c.attachments)==1:
                self.checks['selected_text_attachment']=c.attachments[0]['name']==self.file.name
                c.removeAttachment(0);self.checks['remove_chip_keeps_file']=not c.attachments and self.file.is_file()
                c.attachFile(QUrl.fromLocalFile(str(self.file.resolve())));self.stage=3
            elif self.stage==3 and len(c.attachments)==1:
                c.setDraft('Return only the calibration marker in the attached text.');c.draftChanged.emit()
                self.window.grabWindow().save(str(self.directory/'attachment-draft.png'))
                c.send();self.stage=4
            elif self.stage==4 and not c.busy and any(r['role']=='assistant' and r['status']=='completed' for r in c.messages.rows):
                c.rename('Desktop acceptance attachment')
                self.checks['attachment_reached_shared_brain']='quartz-731' in c.messages.rows[-1]['content']
                self.checks['accepted_clears_attachment_draft']=not c.attachments
                self.message_ids=[r['message_id'] for r in c.messages.rows]
                self.window.grabWindow().save(str(self.directory/'attachment-response.png'))
                c.archiveSelected(True);c.showArchive(True);self.stage=5
            elif self.stage==5 and any(r['conversation_id']==self.cid for r in c.chats):
                # Wait for the canonical archived list, not the prior list snapshot.
                if not c.worker.archived or monotonic()-c.worker.last_chats>1:return
                self.checks['archive_preserves_messages']=self.message_ids==[r['message_id'] for r in c.messages.rows]
                c.archiveSelected(False);c.showArchive(False);self.stage=6;self.stamp=monotonic()
            elif self.stage==6 and monotonic()-self.stamp>3 and any(r['conversation_id']==self.cid for r in c.chats):
                self.checks['archive_restored']=True
                if c.voiceState!='listening':return
                c.preference('reducedMotion',False);c.startLive();self.stage=7
            elif self.stage==7 and self.ack and c.liveActive:
                self.checks['live_backend_ack_keeps_window']=self.window.isVisible()
                self.checks['same_conversation_during_live']=c.selected==self.cid
                self.window.grabWindow().save(str(self.directory/'inline-live.png'))
                c.stopLive();self.stage=8
            elif self.stage==8 and not c.liveActive:
                self.checks['live_stopped_cleanly']=c.ready
                self.checks['same_conversation_after_live']=c.selected==self.cid
                self.checks['same_backend_identity']=c.worker.transport.metadata['identity']==self.identity['identity']
                self.window.grabWindow().save(str(self.directory/'live-return.png'))
                c.setDraft('Workflow restart draft — Оникс 🪐');c.draftChanged.emit()
                self.stage=9;self.stamp=monotonic()
            elif self.stage==9 and monotonic()-self.stamp>.8:self.finish()
        except Exception as exc:self.finish(type(exc).__name__+': '+str(exc))
