"""Opt-in, app-owned Qt tests of the shipped QML. No OS input or external apps."""
import json
from pathlib import Path
from PySide6.QtCore import QObject,QPoint,QPointF,QTimer,Qt,QMetaObject
from PySide6.QtTest import QTest,QSignalSpy
from PySide6.QtGui import QGuiApplication

class UIAcceptance:
    def __init__(self,client,window,folder):
        self.client=client;self.window=window;self.path=Path(folder);self.path.mkdir(parents=True,exist_ok=True)
        self.results={};self.stage=0;self.frames=0
        self.timer=QTimer();self.timer.setInterval(800);self.timer.timeout.connect(self.step);self.timer.start()

    def shot(self,name):
        if not self.window.grabWindow().save(str(self.path/(name+'.png'))):raise RuntimeError('Screenshot failed')

    def point(self,item):return item.mapToScene(QPointF(item.width()/2,item.height()/2)).toPoint()

    def step(self):
        try:
            w=self.window
            if self.stage==0:
                self.live=w.findChild(QObject,'liveMic');self.mic=w.findChild(QObject,'composerMic')
                self.spy=QSignalSpy(self.live.clicked)
                self.shot('normal');self.results['empty_preview']=not self.client.chats and not self.client.messages.rows
            elif self.stage==1:QTest.mouseMove(w,self.point(self.live))
            elif self.stage==2:
                self.results['hover_round']=self.live.property('hovered') and self.live.property('round')
                self.shot('hover-live')
                QTest.mousePress(w,Qt.LeftButton,Qt.NoModifier,self.point(self.live))
                self.shot('pressed')
                QTest.mouseRelease(w,Qt.LeftButton,Qt.NoModifier,self.point(self.live))
                self.results['one_click']=self.spy.count()==1
            elif self.stage==3:
                self.live.forceActiveFocus();QTest.keyClick(w,Qt.Key_Space)
                self.results['keyboard_one_action']=self.spy.count()==2
                self.shot('focus');self.live.setProperty('enabled',False)
                QTest.mouseClick(w,Qt.LeftButton,Qt.NoModifier,self.point(self.live))
                self.results['disabled_no_action']=self.spy.count()==2;self.shot('disabled')
            elif self.stage==4:
                self.live.setProperty('enabled',True);self.live.setProperty('busy',True)
                self.shot('busy');QTest.mouseMove(w,self.point(self.mic))
            elif self.stage==5:
                self.results['composer_hover_round']=self.mic.property('hovered') and self.mic.property('round')
                self.shot('hover-composer');QTest.mouseMove(w,QPoint(400,60))
            elif self.stage==6:
                QMetaObject.invokeMethod(w.findChild(QObject,'settingsDialog'),'open')
            elif self.stage==7:
                self.shot('settings');QMetaObject.invokeMethod(w.findChild(QObject,'settingsDialog'),'close')
                w.setProperty('acceptanceMotion',True);self.client._prefs['reducedMotion']=False;self.client.changed.emit()
            elif self.stage==8:
                self.shot('animation-start');self.timer.setInterval(200)
            elif 9<=self.stage<=28:
                self.shot('frame-'+str(self.stage-9).zfill(2))
                if self.stage==28:w.hide()
            elif self.stage==29:
                self.results['hidden_motion_stopped']=not w.property('motion');w.show()
                self.client._preview=True
                self.client._prefs['reducedMotion']=True;self.client.changed.emit()
                self.results['reduced_motion_stopped']=not w.property('motion')
            elif self.stage==30:
                self.timer.stop()
                (self.path/'ui-checks.json').write_text(json.dumps(self.results,indent=2),encoding='utf-8')
                w.setProperty('forceQuit',True);QGuiApplication.quit()
            self.stage+=1
        except Exception as exc:
            self.timer.stop();self.client._preview=True
            (self.path/'error.txt').write_text(repr(exc),encoding='utf-8')
            self.window.setProperty('forceQuit',True);QGuiApplication.quit()
