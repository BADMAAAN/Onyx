"""Opt-in, neutral-data test of our own Qt window (no OS input automation).

Run with --acceptance-dir PATH. Drives the same public Client slots as QML and
uses Qt's own window APIs. It never controls another desktop application.
"""
import json
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QTimer, QCoreApplication, QEvent, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QInputMethodEvent


class Acceptance:
    def __init__(self,client,window,directory):
        self.client,self.window=client,window
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.started=monotonic();self.stage=0;self.a='';self.b='';self.checks={};self.stamp=0
        self.pid=None;self.frames=0;self.window.frameSwapped.connect(self.frame)
        self.timer=QTimer();self.timer.setInterval(100);self.timer.timeout.connect(self.step);self.timer.start()
        self.composer=window.findChild(__import__('PySide6.QtCore',fromlist=['QObject']).QObject,'composer')
    def frame(self):self.frames+=1
    def save(self):
        payload=dict(checks=self.checks,conversation_a=self.a,conversation_b=self.b,stage=self.stage,seconds=monotonic()-self.started)
        (self.directory/'gui-acceptance.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    def text(self,value):
        self.composer.setProperty('text',value)
        self.client.setDraft(value)
    def key(self,modifiers=Qt.NoModifier):
        QCoreApplication.sendEvent(self.window,QKeyEvent(QEvent.KeyPress,Qt.Key_Return,modifiers,'\n'))
        QCoreApplication.sendEvent(self.window,QKeyEvent(QEvent.KeyRelease,Qt.Key_Return,modifiers,'\n'))
    def step(self):
        c=self.client;now=monotonic()
        try:
            if now-self.started>180:raise TimeoutError('GUI acceptance timed out at '+str(self.stage))
            if self.stage==0 and c.ready and (not c.selected or c._historyLoaded):
                self.checks['startup_draft_restored']=c.draft=='Draft survives restart — Оникс 🪐\nSecond line'
                self.checks['startup_history_restored']=len(c.messages.rows)>=4
                c.preference("reducedMotion",False)
                c.newChat();self.stage=1;self.stamp=now
            elif self.stage==1 and c.welcome and c.selected and now-self.stamp>1:
                self.a=c.selected;self.checks['welcome']=True
                self.frames=0;self.stamp=now;self.stage=2
            elif self.stage==2 and now-self.stamp>2:
                self.checks['hero_frame_rate_sample']=round(self.frames/(now-self.stamp),1)
                self.window.grabWindow().save(str(self.directory/'welcome.png'))
                self.composer.forceActiveFocus()
                self.text('Unicode Оникс');self.composer.setProperty('cursorPosition',13)
                self.key(Qt.ShiftModifier)
                self.checks['shift_enter_newline']='\n' in self.composer.property('text')
                QCoreApplication.sendEvent(self.window,QInputMethodEvent('composition',[]))
                self.checks['ime_composing']=self.composer.property('inputMethodComposing')
                self.key()
                self.checks['ime_no_submit']=not c.busy
                QCoreApplication.sendEvent(self.window,QInputMethodEvent('',[]))
                self.text('The observatory code in this conversation is violet-184. Explain why planets orbit the Sun in eight short paragraphs.')
                self.checks['draft_keeps_welcome']=c.welcome
                self.key();self.checks['optimistic_user']=any(r['status']=='sending' for r in c.messages.rows)
                self.stage=3;self.stamp=now
            elif self.stage==3 and not c.welcome and any(r['role']=='assistant' and r['content'] for r in c.messages.rows):
                self.checks['hero_released_on_accept']=True
                self.checks['observed_live_partial']=any(r['status']=='running' and r['content'] for r in c.messages.rows)
                c.newChat();self.stage=4
            elif self.stage==4 and c.selected!=self.a and c.welcome:
                self.b=c.selected
                self.text('Черновик Б 🪐\nSecond line')
                c.select(self.a);self.stage=5;self.stamp=now
            elif self.stage==5 and c.selected==self.a and c.messages.rows:
                self.checks['a_history_survives_switch']=True
                c.select(self.b);self.stage=6
            elif self.stage==6 and c.selected==self.b and c.welcome:
                self.checks['per_chat_unicode_draft']=c.draft=='Черновик Б 🪐\nSecond line'
                self.text('The observatory code in this conversation is amber-629. What is the observatory code in this conversation?')
                c.send();self.stage=7
            elif self.stage==7 and not c.busy and any(r['role']=='assistant' and r['status']=='completed' for r in c.messages.rows):
                answer=' '.join(r['content'] for r in c.messages.rows if r['role']=='assistant')
                self.checks['b_own_fact']='amber-629' in answer
                self.checks['b_no_a_fact']='violet-184' not in answer
                c.select(self.a);self.stage=8
            elif self.stage==8 and c.selected==self.a and c.messages.rows and not c.busy:
                self.checks['answer_finished_in_a']=any(r['role']=='assistant' and r['status']=='completed' for r in c.messages.rows)
                self.text('What is the observatory code in this conversation?');c.send();self.stage=9
            elif self.stage==9 and not c.busy and len(c.messages.rows)>=4:
                answer=c.messages.rows[-1]['content']
                self.checks['a_restored_fact']='violet-184' in answer
                self.checks['a_no_b_fact']='amber-629' not in answer
                self.window.grabWindow().save(str(self.directory/'real-chat.png'))
                c.copy('  print("Оникс 🪐")\n');self.checks['clipboard_unicode']=QGuiApplication.clipboard().text()=='  print("Оникс 🪐")\n'
                screens=QGuiApplication.screens()
                self.checks['screens']=[dict(name=s.name(),width=s.geometry().width(),height=s.geometry().height(),dpr=s.devicePixelRatio()) for s in screens]
                self.screenIndex=0;self.stage=10;self.stamp=now
            elif self.stage==10 and now-self.stamp>.6:
                screens=QGuiApplication.screens()
                if self.screenIndex<len(screens):
                    screen=screens[self.screenIndex];self.window.setScreen(screen)
                    g=screen.availableGeometry();self.window.setPosition(g.x()+40,g.y()+40)
                    self.window.resize(min(1024,g.width()-80),min(780,g.height()-80))
                    self.text('Resize check 🪐');self.checks['screen_'+str(self.screenIndex)+'_draft']=c.draft=='Resize check 🪐'
                    self.screenIndex+=1;self.stamp=now
                else:
                    self.window.showMaximized();self.stage=11;self.stamp=now
            elif self.stage==11 and now-self.stamp>.5:
                self.checks['maximize']=self.window.visibility().name=='Maximized'
                self.window.showMinimized();self.stage=12;self.stamp=now
            elif self.stage==12 and now-self.stamp>.6:
                self.checks['minimize']=self.window.visibility().name=='Minimized'
                self.checks['hidden_motion_stopped']=not self.window.property('motion')
                self.window.showNormal();self.stage=13;self.stamp=now
            elif self.stage==13 and now-self.stamp>.6:
                self.checks['restore']=self.window.isVisible()
                self.text('Draft survives restart — Оникс 🪐\nSecond line')
                c.preference('reducedMotion',True);self.checks['reduced_motion']=not self.window.property('motion')
                self.stage=14;self.stamp=now
            elif self.stage==14 and now-self.stamp>.7:
                self.save();self.timer.stop();QGuiApplication.quit()
        except Exception as exc:
            self.checks['error']=type(exc).__name__+': '+str(exc);self.save();self.timer.stop();QGuiApplication.quit()
