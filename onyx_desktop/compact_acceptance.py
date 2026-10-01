"""Isolated production QML/Client checks, also runnable from the shipped exe.

Qt input injection is labelled synthetic; this is not an acoustic or physical
mouse benchmark. No production backend, profile or memory is touched.
"""
import json
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, Qt, QPoint, QPointF, QMetaObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest


class CompactAcceptance:
    def __init__(self,client,window,path):
        self.c,self.w,self.path=client,window,Path(path)
        self.path.mkdir(parents=True,exist_ok=True)
        self.stage=0;self.results={};self.calls=[]
        self.c.command=lambda name,**args:self.calls.append((name,args))
        self.c._capabilities=['dictation'];self.c._prefs['language']='ru';self.c.languageChanged.emit();self.c.changed.emit()
        self.timer=QTimer();self.timer.timeout.connect(self.step);self.timer.start(250)
    def item(self,name):
        item=self.w.findChild(QObject,name)
        if item is None:
            pending=[self.w.contentItem()]
            while pending:
                visual=pending.pop()
                if visual.objectName()==name:item=visual;break
                pending.extend(visual.childItems())
        if item is None:raise RuntimeError('Missing production object '+name)
        return item
    def click(self,name):
        item=self.item(name);p=item.mapToScene(QPointF(item.width()/2,item.height()/2))
        QTest.mouseClick(self.w,Qt.LeftButton,Qt.NoModifier,QPoint(int(p.x()),int(p.y())))
    def invoke(self,name,method):QMetaObject.invokeMethod(self.item(name),method)
    def shot(self,name):self.w.grabWindow().save(str(self.path/(name+'.png')))
    def step(self):
        try:
            c=self.c;w=self.w;composer=self.item('composer')
            if self.stage==0:
                self.shot('normal');c._preview=False
                composer.forceActiveFocus();composer.setProperty('text','Neutral draft')
                composer.setProperty('cursorPosition',13)
            elif self.stage==1:
                self.click('composerMic');self.results['one_dictation_start']=sum(n=='dictation' and a['op']=='dictation.start' for n,a in self.calls)==1
                self.sid=c._dictation['session_id'];c.receive('dictation',dict(session_id=self.sid,state='recording'))
            elif self.stage==2:
                self.shot('dictation');composer.forceActiveFocus();QTest.keyClick(w,Qt.Key_Return)
                self.results['enter_during_recording_no_submit']=not any(n=='submit' for n,a in self.calls)
                self.click('composerMic')
            elif self.stage==3:
                self.shot('transcribing');c.receive('dictation',dict(session_id=self.sid,state='ready',text='открой браузер'))
                self.results['transcript_editable']=composer.property('text')=='Neutral draft открой браузер'
                self.results['dictation_zero_requests']=not any(n=='submit' for n,a in self.calls)
                c.receive('dictation',dict(session_id=self.sid,state='ready',text='открой браузер'))
                self.results['dedup_insert']=composer.property('text')=='Neutral draft открой браузер'
                composer.forceActiveFocus();QTest.keyClick(w,Qt.Key_Z,Qt.ControlModifier)
            elif self.stage==4:
                self.results['native_text_undo']=composer.property('text')=='Neutral draft'
                self.click('sendButton');self.results['send_once']=sum(n=='submit' for n,a in self.calls)==1
                self.results['send_keeps_window']=w.isVisible()
                self.results['send_no_live']=not any(n=='voice' for n,a in self.calls)
                self.shot('sent')
                c._pending.clear();c.messages.replace([]);c._preview=True
                w.setProperty('menuTarget',dict(conversation_id='fixture-A',title='Нейтральный чат',pinned=False))
                self.invoke('chatMenu','open')
            elif self.stage==5:
                self.shot('menu');self.invoke('chatMenu','close');self.invoke('deleteDialog','open')
            elif self.stage==6:
                self.shot('delete');QTest.keyClick(w,Qt.Key_Escape)
                self.results['delete_escape_no_mutation']=not any(n=='delete' for n,a in self.calls)
                self.invoke('settingsDialog','open')
            elif 7<=self.stage<=13:
                self.item('settingsDialog').setProperty('page',self.stage-7)
            elif self.stage==14:
                self.shot('settings-data');self.item('settingsDialog').setProperty('page',1)
            elif self.stage==15:
                self.shot('settings-appearance');self.invoke('settingsDialog','close')
                c._pending.clear();c.messages.replace([]);c._preview=False;c._capabilities=['dictation','voice_live']
                composer.setProperty('text','');self.click('sendButton')
            elif self.stage==16:
                self.shot('live');self.results['empty_composer_starts_inline_live']=w.property('liveOpen') and sum(n=='voice' and a['op']=='voice.live' for n,a in self.calls)==1
                self.results['inline_live_preserves_chat']=w.isVisible() and not self.item('composer').property('text')
                self.click('stopLive');self.results['inline_live_clean_stop']=not w.property('liveOpen') and any(n=='voice' and a['op']=='voice.stopLive' for n,a in self.calls)
                c._preview=True;self.invoke('settingsDialog','open')
                self.item('settingsSearch').setProperty('text','Исключения')
            elif self.stage==17:
                self.results['settings_russian_search']=self.item('settingsDialog').property('page')==3
                self.shot('settings-search');self.item('settingsSearch').setProperty('text','DuckDuckGo')
            elif self.stage==18:
                self.results['settings_description_search']=self.item('settingsDialog').property('page')==4
                self.item('settingsSearch').setProperty('text','Output device')
            elif self.stage==19:
                self.results['settings_english_search']=self.item('settingsDialog').property('page')==2
                self.invoke('settingsDialog','close');c.receive('projects',[dict(project_id='fixture-project',title='Идеи')])
                c.receive('chats',[dict(conversation_id='fixture-drag',title='Переместить этот чат',pinned=False,archived=False)])
            elif self.stage==20:
                item=self.item('chat:fixture-drag');self.drag_start=item.mapToScene(QPointF(60,item.height()/2))
                target=self.item('project:fixture-project');self.drag_end=target.mapToScene(QPointF(65,target.height()/2))
                QTest.mousePress(w,Qt.LeftButton,Qt.NoModifier,self.drag_start.toPoint())
                QTest.mouseMove(w,(self.drag_start+QPointF(20,0)).toPoint(),20)
            elif self.stage==21:
                QTest.mouseMove(w,self.drag_end.toPoint(),40)
            elif self.stage==22:
                self.shot('project-drop-target');QTest.mouseRelease(w,Qt.LeftButton,Qt.NoModifier,self.drag_end.toPoint())
            elif self.stage==23:
                self.results['drag_uses_canonical_move']=sum(n=='project' and a.get('op')=='desktop.moveToProject' and a.get('conversation_id')=='fixture-drag' and a.get('project_id')=='fixture-project' for n,a in self.calls)==1
                self.results['drag_creates_no_conversation']=not any(n=='create' for n,a in self.calls)
                self.finish()
            if 8<=self.stage<=14:self.shot('settings-page-'+str(self.stage-8))
            self.stage+=1
        except Exception as exc:
            self.results['error']=repr(exc);self.finish()
    def finish(self):
        self.timer.stop();self.c._preview=True
        (self.path/'checks.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2),encoding='utf-8')
        self.w.setProperty('forceQuit',True);QGuiApplication.quit()
