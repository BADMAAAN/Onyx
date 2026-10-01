"""Exercise the real canonical QML component without a backend or visible UI."""
import os
from pathlib import Path
import subprocess
import sys
from unittest import TestCase


class PearlMotionTests(TestCase):
    def test_spectrum_drifts_and_reduced_motion_stops_all_logo_motion(self):
        code = r'''
from pathlib import Path
from PySide6.QtCore import QObject, QUrl, QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from onyx_desktop.client import Client
app=QGuiApplication([])
client=Client('.', 'unused', preview=True)
engine=QQmlApplicationEngine()
engine.rootContext().setContextProperty('client',client)
warnings=[]
engine.warnings.connect(lambda messages: warnings.extend(str(m) for m in messages))
engine.load(QUrl.fromLocalFile(str(Path('onyx_desktop/qml/Pearl.qml').resolve())))
assert engine.rootObjects(), warnings
pearl=engine.rootObjects()[0]
pearl.setProperty('width',144);pearl.setProperty('height',144)
drift=pearl.findChild(QObject,'spectrumDrift')
assert drift is not None
steps=[item for item in drift.children() if item.property('duration') is not None]
assert len(steps)==3 and all(item.property('duration')==45000 for item in steps)
# Shorten only this isolated instance to exercise a complete transition quickly.
for step in steps: step.setProperty('duration',80)
def advance(ms):
    wait=QEventLoop();QTimer.singleShot(ms,wait.quit);wait.exec()
client.preference('reducedMotion',False)
pearl.setProperty('animate',True)
start=pearl.property('spectrumColor')
advance(60)
assert pearl.property('motionAllowed') and drift.property('running')
assert pearl.property('spectrumColor')!=start
client.preference('reducedMotion',True)
assert not pearl.property('motionAllowed') and not drift.property('running')
fixed=pearl.property('spectrumColor')
advance(100)
assert pearl.property('spectrumColor')==fixed
client.preference('reducedMotion',False)
pearl.setProperty('visible',False)
assert not drift.property('running')
assert not warnings, warnings
import shiboken6
shiboken6.delete(engine)
'''
        result = subprocess.run([sys.executable, '-X', 'faulthandler', '-c', code],
                                cwd=Path(__file__).resolve().parents[1],
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
