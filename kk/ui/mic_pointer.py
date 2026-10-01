"""Separate mic hit target: movement can never become a microphone tap."""
from math import hypot


class MicPointer:
    def __init__(self, toggle, start, commit, cancel, threshold=6, hold_seconds=.35):
        self.toggle, self.start, self.commit, self.cancel = toggle, start, commit, cancel
        self.threshold, self.hold_seconds = threshold, hold_seconds
        self.down = None
        self.held = self.moved = False

    def press(self, point, now):
        self.down = (point, now)
        self.held = self.moved = False

    def move(self, point):
        if self.down and hypot(point[0]-self.down[0][0], point[1]-self.down[0][1]) > self.threshold:
            self.moved = True
            if self.held:
                self.cancel()
                self.held = False

    def tick(self, now):
        if self.down and not self.moved and not self.held and now-self.down[1] >= self.hold_seconds:
            self.held = True
            self.start()

    def release(self, point, cancel=False):
        if self.down is None: return
        self.move(point)
        if cancel:
            if self.held: self.cancel()
        elif not self.moved:
            if self.held: self.commit()
            else: self.toggle()
        self.down = None
        self.held = False


def mic_hit(x, y, size):
    return (x-size*.5)**2+(y-size*.74)**2 <= (size*.105)**2
