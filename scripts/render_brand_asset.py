"""Render the shared QML brand at icon resolution, without redesigning the source."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QUrl,QTimer,QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQml import QQmlApplicationEngine,QQmlExpression
from onyx_desktop.client import Client

app=QGuiApplication([])
client=Client(ROOT,ROOT/'build/unused-brand-profile.json',preview=True)
engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('client',client)
qml='''import QtQuick
import QtQuick.Window
import "../onyx_desktop/qml"
Window { width: 256; height: 256; visible: true; color: "transparent"
    Pearl { width: 256; height: 256; animate: false }
}'''
engine.loadData(qml.encode(),QUrl.fromLocalFile(str(ROOT/'scripts/Brand.qml')))
if not engine.rootObjects():raise SystemExit('Brand component failed')
window=engine.rootObjects()[0]
def save():
    destination=(ROOT/'onyx_desktop/assets/pearl.png').as_posix()
    image=window.grabWindow()
    if image.isNull() or image.pixelColor(128,128).alpha()<240 or image.pixelColor(0,0).alpha()!=0 or not image.save(destination):print('Brand render export failed');app.exit(1)
    else:app.quit()
QTimer.singleShot(1600,save)
QTimer.singleShot(5000,lambda:app.exit(2))
raise SystemExit(app.exec())
