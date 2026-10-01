"""Self-test the production QML scene on real displays, without speech input."""
import ctypes
import json
import os
from pathlib import Path
from time import perf_counter as monotonic
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QWindow

def exercise(app, bridge, window):
    import psutil
    process=psutil.Process(); process.cpu_percent()
    foreground=ctypes.windll.user32.GetForegroundWindow()
    def owns_focus():
        pid=ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(),ctypes.byref(pid))
        return pid.value==os.getpid()
    # Render tests are independent of whatever content the user has in foreground.
    bridge.config.values.setdefault('orb',{})['hide_in_fullscreen']=False
    output=bridge.config.root/'data'/'gpu-check'; output.mkdir(parents=True,exist_ok=True)
    results=[]; jobs=[]; screen_rows=[]
    def record(label):
        row=dict(label=label, visible=window.isVisible(), orb=bridge.value.get('orb'), edge=bridge.value.get('edge'),
            state=bridge.value.get('state'), monitor=window.screen().name(),dpr=window.devicePixelRatio(),
            backend=str(window.rendererInterface().graphicsApi()),
            no_focus_stolen=not owns_focus(),foreground_unchanged=foreground==ctypes.windll.user32.GetForegroundWindow())
        edges=window.findChildren(type(window),'edgeWindow')
        edge=edges[0] if edges else None
        row['edge_windows']=len(edges)
        row['edge_visible_count']=sum(e.isVisible() for e in edges)
        row['edge_visible']=edge.isVisible() if edge else None
        if edge and label=='hidden_active':
            row['edge_no_activate']=bool(ctypes.windll.user32.GetWindowLongW(int(edge.winId()),-20)&0x08000000)
            row['edge_clickthrough']=bool(ctypes.windll.user32.GetWindowLongW(int(edge.winId()),-20)&0x20)
            edge.grabWindow().save(str(output/'edge.png'))
        results.append(row)
    for index,screen in enumerate(app.screens(),1):
        def place(screen=screen,index=index):
            rect=screen.availableGeometry(); window.setScreen(screen)
            window.setPosition(rect.x()+80,rect.y()+80)
            bridge.receive(dict(visible=True,active=True,state='LISTENING',amplitude=.2))
        jobs.append((place,3500))
        def capture(index=index):
            record('monitor_'+str(index))
            pixels=window.grabWindow()
            pixels.save(str(output/f'orb_{index}.png'))
            pixels.save(str(output/f'orb_{index}_dpr_{window.devicePixelRatio()}.png'))
            import statistics
            screen_rows.append({'monitor':window.screen().name(),'dpr':window.devicePixelRatio(),
                'framebuffer_pixels':[pixels.width(),pixels.height()],
                'frame_ms_median':statistics.median(bridge.frames)*1000 if bridge.frames else None,
                'frame_ms_p95':sorted(bridge.frames)[int((len(bridge.frames)-1)*.95)]*1000 if bridge.frames else None})
            bridge.frames=[]
        jobs.append((capture,50))
    for state in ('RECORDING','THINKING','ACTING','SPEAKING','SLEEPING'):
        jobs.append((lambda state=state:bridge.receive(dict(visible=True,active=True,state=state,amplitude=.45)),350))
        jobs.append((lambda state=state:record(state),20))
    jobs.append((lambda:bridge.receive(dict(visible=False,active=True,state='SPEAKING',amplitude=.4)),800))
    jobs.append((lambda:record('hidden_active'),20))
    jobs.append((lambda:bridge.receive(dict(visible=False,active=False,state='SLEEPING',amplitude=0)),800))
    jobs.append((lambda:record('hidden_sleeping'),20))
    jobs.append((lambda:bridge.receive(dict(visible=True,active=False,state='IDLE',amplitude=0)),500))
    jobs.append((lambda:record('shown'),20))
    fullscreen_fixture=QWindow()
    fullscreen_fixture.setFlags(Qt.Tool|Qt.FramelessWindowHint|Qt.WindowDoesNotAcceptFocus)
    fullscreen_fixture.setOpacity(0)
    def fill_screen():
        fullscreen_fixture.setScreen(window.screen())
        fullscreen_fixture.setGeometry(window.screen().geometry())
        fullscreen_fixture.show()
    def check_fullscreen():
        from .orb_window import foreground_info
        from .orb_model import is_fullscreen
        from kk.tools.windows.native import monitor_for_window
        hwnd=int(fullscreen_fixture.winId())
        info=foreground_info(hwnd)
        results.append({'label':'native_fullscreen_detection',
            'detected':is_fullscreen(info,monitor_for_window(hwnd)), 'bounds':info['bounds'],
            'no_focus_stolen':not owns_focus()})
        fullscreen_fixture.hide(); fullscreen_fixture.destroy()
    jobs.append((fill_screen,250)); jobs.append((check_fullscreen,250))
    # Window movement benchmark measures Qt submission/presentation, not physical mouse latency.
    rect=app.primaryScreen().availableGeometry()
    moves=[]
    for index in range(120):
        def move(index=index):
            started=monotonic(); window.setPosition(rect.x()+80+index*2,rect.y()+80)
            moves.append((monotonic()-started)*1000)
        jobs.append((move,16))
    def finish():
        import statistics
        result={'checks':results,'screens':screen_rows,'cpu_percent_one_core':process.cpu_percent(),
            'move_submission_ms_median':statistics.median(moves),'move_submission_ms_p95':sorted(moves)[113],
            'movement_basis':'120 programmatic QWindow moves at 16ms; not physical pointer-to-photon',
            'renderer':'Qt Quick / Direct3D11 / threaded scene graph'}
        hidden=next(r for r in results if r.get('label')=='hidden_active')
        result['passed']=all(r.get('no_focus_stolen') for r in results) and all(s['frame_ms_median'] for s in screen_rows) and hidden['edge_visible_count']==len(app.screens()) and hidden.get('edge_clickthrough') and hidden.get('edge_no_activate') and next(r['detected'] for r in results if r.get('label')=='native_fullscreen_detection')
        (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        (output/f'result-dpr-{window.devicePixelRatio()}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        bridge.out.put({'self_test':result})
        app.quit()
    jobs.append((finish,1))
    def next_job():
        if not jobs: return
        action,delay=jobs.pop(0)
        try: action()
        except Exception as exc:
            results.append({'error':repr(exc)}); app.quit(); return
        QTimer.singleShot(delay,next_job)
    QTimer.singleShot(500,next_job)
