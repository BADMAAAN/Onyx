"""Pure Edge policy; no Qt, capture, microphone, or desktop side effects."""
from dataclasses import dataclass


ACTIVE_STATES = frozenset({'LISTENING', 'USER_SPEAKING', 'RECORDING', 'COMMITTING',
                           'THINKING', 'ACTING', 'SPEAKING'})


def edge_presence(*, user_visual_hidden=False, fullscreen_hidden=False,
                  readiness=False, active=False, explicitly_activated=False,
                  fullscreen_voice_edge=True):
    # Readiness is reported separately: it must not generate a fake conversation.
    return dict(edge=bool(not user_visual_hidden and active and
                          (not fullscreen_hidden or
                           (explicitly_activated and fullscreen_voice_edge))),
                readiness=bool(readiness), user_visual_hidden=bool(user_visual_hidden),
                fullscreen_hidden=bool(fullscreen_hidden))


def voice_energy(state, microphone=0., playback=0.):
    """Only our playback meter drives SPEAKING; listening ignores ambient music."""
    value = playback * 6 if state == 'SPEAKING' else (
        microphone * 12 if state in {'USER_SPEAKING', 'RECORDING'} else 0.)
    return round(max(0., min(1., float(value))), 3)


@dataclass
class EdgeSession:
    activation_serial: int = 0
    wave_serial: int = 0
    active: bool = False
    explicit: bool = False
    active_screen: str = ''
    error_until: float = 0.
    activation_until: float = 0.
    last_state: str = ''

    def update(self, packet, now, screen):
        state = packet.get('state', 'IDLE')
        serial = int(packet.get('activation_serial', 0))
        explicit = serial > self.activation_serial
        if explicit:
            self.activation_serial = serial
            self.explicit = True
            self.wave_serial += 1
            self.active_screen = screen
            self.activation_until = now + .9
        if state == 'ERROR' and self.last_state != 'ERROR':
            self.error_until = now + .85
        active = bool(packet.get('active') or state in ACTIVE_STATES or now < self.activation_until or
                      (state == 'ERROR' and now < self.error_until))
        if active and not self.active and not explicit:
            # Allows text tasks to show state; does not turn them into explicit
            # fullscreen activation or replay the large activation wave.
            self.active_screen = screen
        if not active:
            self.explicit = False
        self.active, self.last_state = active, state
        return active
