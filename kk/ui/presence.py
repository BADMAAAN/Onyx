"""Pure visibility policy shared by GPU overlay and regression tests."""
def presence(visible, active, fullscreen, hide_in_fullscreen=True, fullscreen_voice_edge=True):
    hidden = not visible or (fullscreen and hide_in_fullscreen)
    return {'orb': not hidden, 'edge': bool(hidden and active and (not fullscreen or fullscreen_voice_edge))}
