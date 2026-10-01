"""Client-only validated preferences; history stays backend-owned."""
import json
import os
from pathlib import Path

DEFAULTS = dict(language='ru', reducedMotion=False, spoken=False, textSize=15,
                glow=0.5, showWelcome=True, startup='welcome', closeBehavior='hide',
                backgroundColor='#000000',textColor='#EEEEEE',accentColor='#B8A4FF',fontFamily='Segoe UI',density='compact',backgroundPreset='black',
                livePalette='pearl',liveSize=100,liveSpeed=10,liveBackground=False,sidebarOpen=True,settingsExpanded={},sidebarCollapsed={},
                shortcutNew='Ctrl+N',shortcutSettings='Ctrl+,',shortcutDictation='Ctrl+Shift+D',shortcutStop='Ctrl+Shift+X')
GROUPS = dict(appearance=('reducedMotion','textSize','glow','backgroundColor','textColor','accentColor','fontFamily','density','backgroundPreset'),
              window=('startup','closeBehavior'), voice=('spoken',))

_font_families = None


def installed_font_families():
    """Populate once on the GUI thread; workers only consume the cached set."""
    global _font_families
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QFontDatabase, QGuiApplication
    app = QGuiApplication.instance()
    if _font_families is None and app and QThread.currentThread() == app.thread():
        _font_families = frozenset(QFontDatabase.families())
    return _font_families or frozenset(('Segoe UI',))


def system_font():
    families = installed_font_families()
    return next((name for name in ('Segoe UI Variable', 'Segoe UI Variable Text', 'Segoe UI') if name in families), 'Segoe UI')

def profile_path(identity,legacy,profile=None):
    """Use a real per-user path shared with Explorer, outside MSIX AppData VFS."""
    import shutil
    root=Path(profile or os.environ['USERPROFILE'])/'Onyx'/'Data'/identity
    target=root/'preferences.json'
    legacy=Path(legacy)
    if not target.exists() and legacy.is_file():
        root.mkdir(parents=True,exist_ok=True)
        # Copy only; preserve the original profile as a rollback record.
        shutil.copy2(legacy,root/'preferences.pre-graphite.json')
        temporary=target.with_suffix('.tmp')
        shutil.copy2(legacy,temporary)
        os.replace(temporary,target)
    return target

def validate(key, value):
    if key not in DEFAULTS: raise ValueError('Unknown preference')
    default = DEFAULTS[key]
    if key in ('backgroundColor','textColor','accentColor'):
        import re
        if not isinstance(value,str) or not re.fullmatch('#[0-9a-fA-F]{6}',value):raise ValueError('Use #RRGGBB')
        channels=[int(value[i:i+2],16)/255 for i in (1,3,5)]
        luminance=sum(w*(v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4) for w,v in zip((.2126,.7152,.0722),channels))
        if key=='backgroundColor' and luminance>.035:raise ValueError('Choose a dark background to keep controls readable')
        if key in ('textColor','accentColor') and luminance<.4:raise ValueError('Choose a lighter text/accent color for readable contrast')
    if key=='fontFamily':
        if value not in installed_font_families():raise ValueError('Choose an installed font')
    if key=='density' and value not in ('compact','comfortable'):raise ValueError('Invalid density')
    if key=='backgroundPreset' and value not in ('black','cosmic'):raise ValueError('Invalid background preset')
    if key=='livePalette' and value not in ('pearl','cool','lilac','custom'):raise ValueError('Invalid Live palette')
    if key=='liveSize' and (type(value) not in (int,float) or not 72<=value<=120):raise ValueError('Logo size: 72–120')
    if key=='liveSpeed' and (type(value) not in (int,float) or not 5<=value<=30):raise ValueError('Calm motion: 5–30 seconds')
    if key=='settingsExpanded' and (not isinstance(value,dict) or len(value)>16 or any(not isinstance(k,str) or type(v) is not bool for k,v in value.items())):raise ValueError('Invalid section preferences')
    if key=='sidebarCollapsed' and (not isinstance(value,dict) or len(value)>1024 or any(not isinstance(k,str) or type(v) is not bool for k,v in value.items())):raise ValueError('Invalid sidebar preferences')
    if type(default) is bool and type(value) is not bool: raise ValueError('Expected boolean')
    if key == 'language' and value not in ('ru','en'): raise ValueError('Unknown language')
    if key == 'startup' and value not in ('welcome','last'): raise ValueError('Unknown startup mode')
    if key == 'closeBehavior' and value not in ('hide','quit'): raise ValueError('Unknown close behavior')
    if key == 'textSize' and (type(value) not in (int,float) or value != int(value) or not 12<=value<=22): raise ValueError('Text size must be 12–22')
    if key == 'glow' and (type(value) not in (int,float) or not 0<=value<=1): raise ValueError('Glow must be 0–1')
    if key.startswith('shortcut'):
        from PySide6.QtGui import QKeySequence
        if not isinstance(value,str) or len(value)>50:raise ValueError('Invalid shortcut')
        sequence=QKeySequence.fromString(value,QKeySequence.PortableText)
        value=sequence.toString(QKeySequence.PortableText)
        if sequence.count()!=1 or not value or not ('Ctrl+' in value or 'Alt+' in value):raise ValueError('Use one Ctrl/Alt shortcut')
        if value in ('Ctrl+Space','Ctrl+Alt+Pause','Ctrl+C','Ctrl+V','Ctrl+X','Ctrl+Z','Ctrl+Y','Ctrl+A','Alt+F4'):raise ValueError('Shortcut conflicts with recording, editing or Windows')
    return value

def validate_shortcut_conflict(key,value,prefs,global_hotkey=''):
    if not key.startswith('shortcut'):return
    from PySide6.QtGui import QKeySequence
    normalize=lambda s:QKeySequence.fromString(s,QKeySequence.PortableText).toString(QKeySequence.PortableText).casefold()
    others=[prefs.get(k,v) for k,v in DEFAULTS.items() if k.startswith('shortcut') and k!=key]
    if global_hotkey:others.append(global_hotkey)
    if normalize(value) in {normalize(v) for v in others}:raise ValueError('Shortcut conflicts with another Onyx action')

def atomic_save(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(dict(values, schema=1), stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
