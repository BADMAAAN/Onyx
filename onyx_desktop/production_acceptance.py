"""Opt-in same installed Client/transport with neutral fixture scope and own prefs."""
import json
from pathlib import Path
from time import monotonic
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication


class ProductionAcceptance:
    def __init__(self,client,window,destination):
        self.c,self.w=client,window;self.path=Path(destination);self.path.mkdir(parents=True,exist_ok=True)
        self.report=self.path/'production.json';self.state=json.loads(self.report.read_text(encoding='utf-8')) if self.report.exists() else {'checks':{},'phase':'first'}
        self.stage=0;self.started=monotonic();self.stamp=self.started
        self.timer=QTimer();self.timer.timeout.connect(self.tick);self.timer.start(100)
    def save(self):self.report.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8')
    def check(self,key,value):self.state['checks'][key]=value;self.save()
    def shot(self,name):self.w.grabWindow().save(str(self.path/(name+'.png')))
    def finish(self,error=None):
        if error:self.state['error']=repr(error)
        self.save();self.timer.stop();self.w.setProperty('forceQuit',True);QGuiApplication.quit()
    def tick(self):
        c=self.c;now=monotonic()
        try:
            if now-self.started>240:raise TimeoutError('Production acceptance stage '+str(self.stage)+'; '+c.error)
            if self.stage==0 and c.ready and c._historyLoaded:
                if self.state['phase']=='restart':
                    self.check('restart_same_history',c.selected==self.state['cid'] and len(c.messages.rows)==4)
                    self.check('restart_draft',c.draft=='Нейтральный черновик — Onyx 🪐')
                    self.check('restart_preferences',c.appearance['livePalette']=='cool' and c.appearance['settingsExpanded'].get('Models') is True)
                    self.stage=10
                else:
                    c.newChat();self.state['models_before']=c.models;self.stage=1
            elif self.stage==1 and c.welcome and c._historyLoaded:
                self.state['cid']=c.selected;self.save();c.selectModel('qwen3:14b');self.stage=2
            elif self.stage==2 and c.currentModel=='qwen3:14b' and not c.modelChanging:
                c.setDraft('Answer briefly: what is two plus three?');c.draftChanged.emit();c.send();self.stage=3
            elif self.stage==3 and not c.busy and len(c.messages.rows)==2 and c.messages.rows[-1]['status']=='completed':
                identity=c.messages.rows[-1].get('model_identity',{})
                self.check('installed_model_A',identity.get('actual_model')=='qwen3:14b' and identity.get('response_completed') is True)
                self.shot('model-A');c.selectModel('qwen3-vl:8b');self.stage=4
            elif self.stage==4 and c.currentModel=='qwen3-vl:8b' and not c.modelChanging:
                c.setDraft('What arithmetic result did you give in the previous reply? Answer briefly.');c.draftChanged.emit();c.send();self.stage=5
            elif self.stage==5 and not c.busy and len(c.messages.rows)==4 and c.messages.rows[-1]['status']=='completed':
                identity=c.messages.rows[-1].get('model_identity',{})
                self.check('installed_model_B_same_history',identity.get('actual_model')=='qwen3-vl:8b' and identity.get('response_completed') is True)
                self.state['messages']=c.messages.rows;self.shot('model-B')
                c.saveProject('Observatory notes','','');c.pinChat(c.selected,True)
                c.setDraft('Нейтральный черновик — Onyx 🪐');c.draftChanged.emit();c.preference('startup','last');c.preference('livePalette','cool');c.expandSettings('Models',True)
                self.state['phase']='restart';self.stage=6;self.stamp=now
            elif self.stage==6 and now-self.stamp>1.5 and c.projects:
                self.state['project']=c.projects[0]['project_id'];c.moveChat(c.selected,self.state['project']);self.stage=7;self.stamp=now
            elif self.stage==7 and now-self.stamp>1.5:
                self.check('project_assignment',any(r.get('project_id')==self.state['project'] for r in c.chats));self.finish()
            elif self.stage==10 and c.worker.transport.metadata.get('build',{}).get('acceptance_no_audio'):
                self.state['audio_validation']='Not run: isolated backend intentionally opens no microphone or playback'
                self.check('restart_project',any(r['project_id']==self.state['project'] for r in c.projects))
                self.state['phase']='completed';self.finish()
            elif self.stage==10 and c.voiceState=='listening':
                c.startDictation(len(c.draft));self.stage=11
            elif self.stage==11 and c.dictationState=='recording':
                c.cancelDictation();self.check('dictation_start_cancel_preserves_draft',c.draft=='Нейтральный черновик — Onyx 🪐');self.stage=12;self.stamp=now
            elif self.stage==12 and now-self.stamp>1 and c.voiceState=='listening':
                c.startLive();self.stage=13
            elif self.stage==13 and c.liveActive:
                self.check('live_ack_keeps_window_and_chat',self.w.isVisible() and c.selected==self.state['cid']);self.shot('live-in-app');c.stopLive();self.stage=14
            elif self.stage==14 and not c.liveActive:
                self.check('stop_live_backend_alive',c.ready);self.check('restart_project',any(r['project_id']==self.state['project'] for r in c.projects));self.state['phase']='completed';self.finish()
            elif self.stage==15 and c.selected!=self.state['cid']:
                self.check('fixture_removed',True);self.state['phase']='completed';self.finish()
        except Exception as exc:self.finish(exc)
