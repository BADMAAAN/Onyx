"""Isolated actual QML scenes, fed only the explicitly supplied neutral report."""
import json
from pathlib import Path
from PySide6.QtCore import QObject,QTimer,QMetaObject,Qt,QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest


class BlackAcceptance:
    def __init__(self,client,window,destination,fixture):
        self.c,self.w=client,window;self.path=Path(destination);self.path.mkdir(parents=True,exist_ok=True)
        self.fixture=json.loads(Path(fixture).read_text(encoding='utf-8')) if fixture else {}
        self.stage=0;self.checks={};self.timer=QTimer();self.timer.timeout.connect(self.tick);self.timer.start(450)
        self.c._prefs.update(language='ru',settingsExpanded={});self.c.languageChanged.emit();self.c.changed.emit()
    def item(self,name):
        item=self.w.findChild(QObject,name)
        if item is None:raise RuntimeError(name+' not found')
        return item
    def invoke(self,name,method):QMetaObject.invokeMethod(self.item(name),method)
    def shot(self,name):
        self.w.grabWindow().save(str(self.path/(name+'.png')))
    def tick(self):
        try:
            if self.stage==0:
                self.shot('empty')
                self.checks['black_background']=self.w.grabWindow().pixelColor(self.w.width()-100,250).name()=='#000000'
                rows=self.fixture.get('models_before',[])
                self.c.receive('models',dict(rows=rows,jobs=[],version='0.34.4',error='',conversation_id=self.c.selected,model_id='qwen3:14b'))
                self.invoke('modelPicker','open')
            elif self.stage==1:
                self.shot('selector');self.invoke('modelPicker','close');self.invoke('settingsDialog','open')
            elif self.stage==2:
                self.shot('settings-collapsed');self.c.expandSettings('Models',True)
            elif self.stage==3:
                self.shot('models');self.item('settingsSearch').setProperty('text','Голос')
            elif self.stage==4:
                self.shot('settings-search');self.checks['search_visible']=bool(self.item('settingsDialog').property('visible'));self.invoke('settingsDialog','close')
                self.w.setProperty('section','Apps')
                self.c._apps=[dict(id='terminal',name='Terminal',publisher='Microsoft',purpose='Local shell',docs='https://learn.microsoft.com/powershell/',installed=True,connected=True,launchable=True),dict(id='codex',name='Codex CLI',publisher='OpenAI',purpose='External coding CLI · separate account',docs='https://developers.openai.com/codex/cli/',installed=True,connected=False,launchable=False)]
                self.c.appsChanged.emit();self.c.changed.emit()
            elif self.stage==5:
                self.shot('apps');self.invoke('settingsDialog','open');self.c.expandSettings('Data',True)
            elif self.stage==6:
                self.shot('info');self.w.setProperty('section','Chat');self.w.setProperty('liveOpen',True)
            elif self.stage==7:
                self.shot('live');self.checks['live_view_keeps_window']=self.w.isVisible();self.w.setProperty('liveOpen',False)
                turns=self.fixture.get('turns',[])
                if turns:
                    from .client import prepare
                    history=turns[-1]['history'];self.c._selected=history['conversation_id'];self.c._historyLoaded=True
                    self.c.selectionChanged.emit();self.c.currentModelChanged.emit()
                    self.c.messages.replace(prepare(history['messages']));self.c.changed.emit();self.c.contentChanged.emit()
            elif self.stage==8:
                self.shot('real-conversation');self.c.preference('sidebarOpen',False)
            elif self.stage==9:
                self.shot('sidebar-collapsed');self.checks['sidebar_preference']=not self.c.appearance['sidebarOpen'];self.finish()
            self.stage+=1
        except Exception as exc:self.checks['error']=repr(exc);self.finish()
    def finish(self):
        self.timer.stop();(self.path/'checks.json').write_text(json.dumps(self.checks,indent=2),encoding='utf-8')
        self.w.setProperty('forceQuit',True);QGuiApplication.quit()
