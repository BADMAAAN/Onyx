"""Random pixel fixtures + scoring for the coordinator's explicit live probe.

Display mode only opens our own windows; it never activates a user application.
Probe mode uses the running Onyx's authenticated production command queue.
Importing this module does not open windows, capture pixels, or load any model.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import random
import re
import sys
from time import perf_counter

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COLORS = {'red': ('#d52929', ('красн', 'red')), 'blue': ('#205cde', ('син', 'blue')),
          'green': ('#188941', ('зел', 'green')), 'orange': ('#e37812', ('оранж', 'orange'))}
SHAPES = {'circle': ('круг', 'окружност', 'circle'), 'square': ('квадрат', 'square'),
          'triangle': ('треуголь', 'triangle')}


def create_fixture():
    rng = random.SystemRandom()
    colors = rng.sample(list(COLORS), 2)
    shapes = rng.sample(list(SHAPES), 2)
    code = str(rng.randrange(10000, 99999))
    image = Image.new('RGB', (1100, 680), '#fafafa')
    draw = ImageDraw.Draw(image)
    for index, (color, shape) in enumerate(zip(colors, shapes)):
        left = 110 + index*550
        box = (left, 200, left+300, 500)
        fill = COLORS[color][0]
        if shape == 'circle':
            draw.ellipse(box, fill=fill)
        elif shape == 'square':
            draw.rectangle(box, fill=fill)
        else:
            draw.polygon([(left+150, 190), (left, 500), (left+300, 500)], fill=fill)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 80)
    draw.text((400, 65), code, fill='#171717', font=font)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    image.close()
    return buffer.getvalue(), {'code': code, 'colors': colors, 'shapes': shapes}


def score(expected, answer):
    text = (answer or '').casefold()
    code_ok = bool(re.search(r'(?<!\d)' + re.escape(expected['code']) + r'(?!\d)', text))
    color_ok = all(any(part in text for part in COLORS[color][1]) for color in expected['colors'])
    shape_ok = all(any(part in text for part in SHAPES[shape]) for shape in expected['shapes'])
    return dict(code=code_ok, colors=color_ok, shapes=shape_ok, passed=code_ok and color_ok and shape_ok)


def match_screen(monitor, candidates):
    """Qt may expose a friendly device name instead of Windows DISPLAYn.

    Qt on Windows keeps native screen origins while scaling each screen's
    dimensions independently. Accept an explicitly scaled origin as a fallback
    for bindings/platforms that expose fully logical virtual coordinates.
    """
    named = [screen for screen in candidates if screen.name() == monitor['monitor_id']]
    if len(named) == 1:
        return named[0]
    scored = []
    for screen in candidates:
        geometry, ratio = screen.geometry(), screen.devicePixelRatio()
        size_error = (abs(round(geometry.width()*ratio) - monitor['width']) +
                      abs(round(geometry.height()*ratio) - monitor['height']))
        origins = [(geometry.x(), geometry.y()),
                   (round(geometry.x()*ratio), round(geometry.y()*ratio))]
        origin_error = min(abs(x-monitor['left']) + abs(y-monitor['top']) for x, y in origins)
        scored.append((size_error + origin_error, screen))
    scored.sort(key=lambda item: item[0])
    if not scored or scored[0][0] > 4 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        raise RuntimeError(f'Ambiguous Qt/native screen geometry for {monitor["monitor_id"]}')
    return scored[0][1]


def display(folder):
    # Neutral title and a painted image: no accessible text, filename, prompt,
    # or control label contains the hidden answers.
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtWidgets import QApplication, QWidget
    from kk.tools.windows.native import list_monitors
    app = QApplication([])
    folder.mkdir(parents=True, exist_ok=True)
    for marker in ('stop', 'refresh'):
        (folder/marker).unlink(missing_ok=True)
    monitors = list_monitors()
    windows = []
    expected = {}
    class Card(QWidget):
        def __init__(self, monitor, screen):
            super().__init__()
            self.monitor = monitor
            self.setWindowTitle('Onyx pixel verification')
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.winId()
            self.windowHandle().setScreen(screen)
            geometry = screen.availableGeometry()
            width, height = min(1100, geometry.width()-80), min(680, geometry.height()-80)
            self.setGeometry(geometry.x()+40, geometry.y()+40, width, height)
            self.refresh()
        def refresh(self):
            pixels, truth = create_fixture()
            path = folder / f'frame_{self.monitor["index"]}.png'
            path.write_bytes(pixels)
            self.picture = QPixmap()
            self.picture.loadFromData(pixels)
            expected[str(self.monitor['index'])] = truth
            self.update()
        def paintEvent(self, event):
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self.picture)
    available_screens = list(app.screens())
    for monitor in monitors:
        screen = match_screen(monitor, available_screens)
        available_screens.remove(screen)
        card = Card(monitor, screen)
        card.show()
        windows.append(card)
    def write_state():
        (folder/'expected.json').write_text(json.dumps(expected), encoding='utf-8')
        (folder/'ready.json').write_text(json.dumps({'monitors': monitors}), encoding='utf-8')
    def commands():
        if (folder/'stop').exists():
            app.quit()
        if (folder/'refresh').exists():
            (folder/'refresh').unlink()
            for window in windows:
                window.refresh()
            write_state()
    write_state()
    timer = QTimer(); timer.timeout.connect(commands); timer.start(200)
    QTimer.singleShot(600_000, app.quit)
    app.exec()


def probe(folder):
    from kk.core.control import request_control
    expected = json.loads((folder/'expected.json').read_text(encoding='utf-8'))
    results = []
    for index, truth in expected.items():
        started = perf_counter()
        # Contains only requested report fields, never answers or image paths.
        question = (f'Посмотри на монитор {index}. На нём тестовая карточка. '
                    'Прочитай код и назови цвет и форму каждой фигуры на карточке.')
        result = request_control(ROOT, 'ask', timeout=210, text=question, spoken=False, diagnostic=True)
        actions = result.get('actions', [])
        observations = [a for a in actions if a.get('tool') in {'see_monitor', 'see_screen'}]
        actual_vision = any((a.get('result', {}).get('data') or {}).get('vision_used') for a in observations
                            if isinstance(a.get('result', {}).get('data'), dict))
        grading = score(truth, result.get('answer'))
        results.append({'monitor': int(index), 'seconds': perf_counter()-started,
                        'score': grading, 'production_vision_used': actual_vision,
                        'passed': grading['passed'] and actual_vision, 'response': result})
    destination = folder/f'production_{__import__("time").time_ns()}.json'
    destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(destination), 'results': [
        {key: value for key, value in row.items() if key != 'response'} for row in results]}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['display', 'probe', 'refresh', 'stop'])
    parser.add_argument('--folder', type=Path, default=ROOT/'data'/'integration_vision')
    args = parser.parse_args()
    if args.operation == 'display':
        display(args.folder)
    elif args.operation == 'probe':
        probe(args.folder)
    else:
        (args.folder/args.operation).touch()
