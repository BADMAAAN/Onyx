def register_windows_tools(*args, **kwargs):
    # Lazy import avoids a cycle: screen capture needs low-level Win32 helpers,
    # while the tool factory also wires the screen-understanding service.
    from .factory import register_windows_tools as implementation
    return implementation(*args, **kwargs)


__all__ = ["register_windows_tools"]
