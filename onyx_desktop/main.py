"""Independent Windows window and singleton; no backend imports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import psutil

from PySide6.QtCore import QUrl, QTimer, QStandardPaths, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from .client import Client


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backend-root')
    parser.add_argument('--performance-dir',help='Opt-in content-free native interaction timing, written on a worker')
    parser.add_argument('--smoke-seconds',type=float,default=0)
    parser.add_argument('--screenshot')
    parser.add_argument('--acceptance-dir')
    parser.add_argument('--workflow-acceptance-dir')
    parser.add_argument('--visual-test',action='store_true',help='Isolated deterministic production scene; no backend or preferences IO')
    parser.add_argument('--ui-acceptance-dir')
    parser.add_argument('--compact-acceptance-dir')
    parser.add_argument('--black-acceptance-dir')
    parser.add_argument('--acceptance-fixture')
    parser.add_argument('--production-acceptance-dir',help='Opt-in neutral backend test with an isolated client preference file')
    parser.add_argument('--visual-size',default='1520x964')
    parser.add_argument('--visual-screen',type=int,default=0)
    args=parser.parse_args()
    if args.ui_acceptance_dir and not args.visual_test:parser.error('--ui-acceptance-dir requires isolated --visual-test')
    if args.compact_acceptance_dir and not args.visual_test:parser.error('--compact-acceptance-dir requires isolated --visual-test')
    if (args.black_acceptance_dir or args.acceptance_fixture) and not args.visual_test:parser.error('Black acceptance requires isolated --visual-test')
    assets=Path(__file__).resolve().parent
    hints=assets/'backend.json'
    configured_root=json.loads(hints.read_text(encoding='utf-8'))['root'] if hints.is_file() else None
    root=args.backend_root or os.environ.get('ONYX_BACKEND_ROOT')
    if not root:root=configured_root
    root=Path(root or assets.parent).resolve()
    from .acceptance_scope import validate_acceptance
    try:acceptance_prefs=validate_acceptance(args,root,protected_root=configured_root)
    except ValueError as exc:parser.error(str(exc))
    QQuickStyle.setStyle('Basic')
    app=QGuiApplication(sys.argv)
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    app.setOrganizationName('Onyx');app.setApplicationName('Onyx Desktop')
    app.setWindowIcon(QIcon(str(assets/'assets'/'pearl.png')))
    identity=hashlib.sha256(str(root).casefold().encode()).hexdigest()[:16]
    name='OnyxDesktop-'+identity
    if acceptance_prefs is not None:name+='-acceptance'
    socket=QLocalSocket()
    if not args.visual_test:
        socket.connectToServer(name)
        if socket.waitForConnected(250):
            socket.write(b'focus');socket.waitForBytesWritten(250);return 0
    server=QLocalServer();server.setSocketOptions(QLocalServer.UserAccessOption)
    if not args.visual_test and not server.listen(name):return 1
    prefs=Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))/identity/'preferences.json'
    if not args.visual_test and acceptance_prefs is None:
        from .preferences import profile_path
        prefs=profile_path(identity,prefs)
    if acceptance_prefs is not None:prefs=acceptance_prefs
    client=Client(root,prefs,preview=args.visual_test)
    engine=QQmlApplicationEngine();engine.warnings.connect(lambda errors: print('\n'.join(e.toString() for e in errors),file=sys.stderr));engine.rootContext().setContextProperty('client',client)
    engine.load(QUrl.fromLocalFile(str(assets/'qml'/'Main.qml')))
    if not engine.rootObjects():client.close();return 2
    window=engine.rootObjects()[0]
    if args.performance_dir:
        from .performance import PerformanceProbe
        probe=PerformanceProbe(window,args.performance_dir,client)
        app.aboutToQuit.connect(probe.close)
    if not args.visual_test:
        from .window_geometry import WindowGeometryController
        geometry_controller=WindowGeometryController(app,window,client)
    if args.visual_test:
        width,height=map(int,args.visual_size.split('x'))
        screen=app.screens()[args.visual_screen]
        window.setScreen(screen);window.setPosition(screen.availableGeometry().topLeft())
        window.resize(width,height);window.setProperty('localHour',18)
    def focus():
        connection=server.nextPendingConnection()
        if connection:connection.close()
        if window.visibility()==QWindow.Minimized:window.showNormal()
        window.show();window.raise_();window.requestActivate()
    server.newConnection.connect(focus)
    if args.acceptance_dir:
        from .acceptance import Acceptance
        acceptance=Acceptance(client,window,args.acceptance_dir)
    if args.workflow_acceptance_dir:
        from .workflow_acceptance import WorkflowAcceptance
        acceptance=WorkflowAcceptance(client,window,args.workflow_acceptance_dir)
    if args.ui_acceptance_dir:
        from .ui_acceptance import UIAcceptance
        acceptance=UIAcceptance(client,window,args.ui_acceptance_dir)
    if args.compact_acceptance_dir:
        from .compact_acceptance import CompactAcceptance
        acceptance=CompactAcceptance(client,window,args.compact_acceptance_dir)
    if args.black_acceptance_dir:
        from .black_acceptance import BlackAcceptance
        acceptance=BlackAcceptance(client,window,args.black_acceptance_dir,args.acceptance_fixture)
    if args.production_acceptance_dir:
        from .production_acceptance import ProductionAcceptance
        acceptance=ProductionAcceptance(client,window,args.production_acceptance_dir)
    if args.screenshot:
        def capture():
            destination=Path(args.screenshot).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
            shot=window.grabWindow()
            if not shot.save(str(destination)):raise RuntimeError('Qt window capture failed')
            destination.with_suffix('.json').write_text(json.dumps(dict(executable=psutil.Process().exe(),pid=os.getpid(),
                visual_test=args.visual_test,logical_size=[window.width(),window.height()],
                physical_size=[shot.width(),shot.height()],dpr=window.devicePixelRatio(),
                screen=window.screen().name(),graphics_api=str(QQuickWindow.graphicsApi())),indent=2),encoding='utf-8')
        QTimer.singleShot(2500,capture)
    if args.smoke_seconds:
        def finish_smoke():
            window.setProperty('forceQuit',True)
            app.quit()
        QTimer.singleShot(int(args.smoke_seconds*1000),finish_smoke)
    app.aboutToQuit.connect(client.close)
    result=app.exec()
    import shiboken6
    shiboken6.delete(engine)
    return result


if __name__=='__main__':raise SystemExit(main())
