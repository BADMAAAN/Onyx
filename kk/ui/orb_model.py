"""Window-independent visibility, geometry and persisted placement."""
import json
import math


def select_monitor(monitors, foreground=None, cursor=None):
    if cursor:
        x, y = cursor
        for m in monitors:
            if m['left'] <= x < m['right'] and m['top'] <= y < m['bottom']:
                return m
    return next((m for m in monitors if m['monitor_id'] == foreground), monitors[0] if monitors else None)


def placement(monitor, size, saved=None):
    left, top, right, bottom = monitor['work_area']
    valid = (isinstance(saved, (list, tuple)) and len(saved) == 2
             and all(isinstance(v, (int, float)) and math.isfinite(v) for v in saved))
    x, y = saved if valid else (right-size[0]-24, bottom-size[1]-24)
    return int(max(left, min(x, right-size[0]))), int(max(top, min(y, bottom-size[1])))


def load_positions(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_positions(path, positions):
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(positions), encoding='utf-8')
        temporary.replace(path)


def is_fullscreen(window, monitor):
    if not monitor or not window.get('hwnd') or window.get('minimized'):
        return False
    if window.get('class_name') in {'Progman', 'WorkerW', 'Shell_TrayWnd'}:
        return False
    bounds = window.get('bounds', ())
    if len(bounds) != 4 or window.get('has_caption', False):
        return False
    return all(abs(a-b) <= 2 for a, b in zip(bounds,
        (monitor['left'], monitor['top'], monitor['right'], monitor['bottom'])))


def hit_orb(x, y, size):
    # Include the visible soft glow; outside it the layered bitmap has alpha 0.
    return (x-size/2)**2+(y-size/2)**2 <= (size*.48)**2
