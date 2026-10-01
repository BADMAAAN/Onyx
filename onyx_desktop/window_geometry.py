"""Keep the native window reachable when the monitor layout changes.

Qt and Windows own interactive moves, resizing, Snap and DPI scaling. This
controller only repairs saved/offscreen bounds after a display-layout change.
It does no filesystem work and never follows pointer movement.
"""
from PySide6.QtCore import QObject, QRect, QSize, QTimer
from PySide6.QtGui import QWindow


def reachable_geometry(saved, areas, primary=0, minimum=(760, 600), margins=(0, 0, 0, 0)):
    """Return a screen index and clamped client rectangle in Qt logical pixels."""
    if (not isinstance(saved, (tuple, list)) or len(saved) != 4
            or any(type(v) is not int or abs(v) > 1000000 for v in saved)
            or saved[2] <= 0 or saved[3] <= 0):
        return None
    available = [(i, QRect(*area)) for i, area in enumerate(areas) if area[2] > 0 and area[3] > 0]
    if not available:
        return None
    box = QRect(*saved)
    overlap = [(box.intersected(area).width() * box.intersected(area).height(), i, area)
               for i, area in available]
    largest = max(overlap, key=lambda item: item[0])
    if largest[0] > 0:
        _, index, area = largest
    else:
        index, area = next(((i, area) for i, area in available if i == primary), available[0])
    left, top, right, bottom = margins
    content_width = max(1, area.width() - left - right)
    content_height = max(1, area.height() - top - bottom)
    width = min(max(minimum[0], box.width()), content_width)
    height = min(max(minimum[1], box.height()), content_height)
    x = max(area.x() + left, min(box.x(), area.right() - right - width + 1))
    y = max(area.y() + top, min(box.y(), area.bottom() - bottom - height + 1))
    return index, (x, y, width, height)


class WindowGeometryController(QObject):
    def __init__(self, app, window, client):
        super().__init__(window)
        self.app, self.window, self.client = app, window, client
        self.minimum = (window.minimumWidth(), window.minimumHeight())
        self.restored = False
        self.layout_changed = False
        self.watched = set()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.recover)
        app.screenAdded.connect(self.watch_screen)
        app.screenRemoved.connect(self.schedule_layout)
        app.primaryScreenChanged.connect(self.schedule_layout)
        window.screenChanged.connect(self.schedule)
        window.visibilityChanged.connect(self.schedule)
        for screen in app.screens():
            self.watch_screen(screen)
        client.changed.connect(self.restore)
        self.restore()

    def watch_screen(self, screen):
        if id(screen) not in self.watched:
            self.watched.add(id(screen))
            screen.availableGeometryChanged.connect(self.schedule_layout)
            screen.geometryChanged.connect(self.schedule_layout)
            screen.logicalDotsPerInchChanged.connect(self.schedule_layout)
        self.schedule_layout()

    def schedule_layout(self, *args):
        self.layout_changed = True
        self.schedule()

    def schedule(self, *args):
        self.timer.start()

    def restore(self):
        if self.restored or not self.client._prefs:
            return
        self.restored = True
        self.apply(self.client._prefs.get('geometry'))

    def recover(self):
        if self.window.visibility() != QWindow.Windowed:
            return
        geometry = self.window.geometry()
        frame = self.window.frameGeometry()
        title = QRect(frame.x(), frame.y(), frame.width(), max(24, self.window.frameMargins().top()))
        reachable = any(title.intersected(screen.availableGeometry()).width() >= 96
                        and title.intersected(screen.availableGeometry()).height() >= 16
                        for screen in self.app.screens())
        # A normal native drag may intentionally span monitors. Never clamp it
        # just because Windows changed the window's current monitor.
        if self.layout_changed or not reachable:
            self.apply((geometry.x(), geometry.y(), geometry.width(), geometry.height()))
        self.layout_changed = False

    def apply(self, saved):
        screens = self.app.screens()
        areas = [screen.availableGeometry().getRect() for screen in screens]
        primary = next((i for i, screen in enumerate(screens) if screen == self.app.primaryScreen()), 0)
        frame = self.window.frameMargins()
        result = reachable_geometry(saved, areas, primary, self.minimum,
                                    (frame.left(), frame.top(), frame.right(), frame.bottom()))
        if result is None:
            return
        index, bounds = result
        screen = screens[index]
        area = screen.availableGeometry()
        # A high-DPI laptop/small work area can be smaller than normal minima.
        self.window.setMinimumSize(QSize(min(self.minimum[0], max(1, area.width() - frame.left() - frame.right())),
                                        min(self.minimum[1], max(1, area.height() - frame.top() - frame.bottom()))))
        geometry = QRect(*bounds)
        if geometry != self.window.geometry():
            if self.window.screen() != screen:
                self.window.setScreen(screen)
            self.window.setGeometry(geometry)
