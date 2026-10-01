"""Fail closed before any automated acceptance can touch a normal profile."""
from hashlib import sha256
from pathlib import Path


BACKEND_MODES = ('acceptance_dir', 'workflow_acceptance_dir', 'production_acceptance_dir')
VISUAL_MODES = ('ui_acceptance_dir', 'compact_acceptance_dir', 'black_acceptance_dir')


def _contained(path, parent):
    resolved = path.resolve()
    if not resolved.is_relative_to(parent):
        raise ValueError('Acceptance data must remain inside the isolated backend root')
    return resolved


def validate_acceptance(args, root, protected_root=None):
    """Return isolated client preferences, or None for ordinary/visual launch.

    Backend storage is fixed by build_runtime at config.root/data. Resolving
    each mutable leaf also catches junctions/symlinks back into real user data.
    Shared model-weight directories are intentionally outside this check.
    """
    modes = [name for name in (*BACKEND_MODES, *VISUAL_MODES) if getattr(args, name, None)]
    if len(modes) > 1:
        raise ValueError('Choose one acceptance mode per client process')
    writing = [name for name in BACKEND_MODES if getattr(args, name, None)]
    if not writing:
        return None
    if getattr(args, 'visual_test', False):
        raise ValueError('Backend acceptance cannot run with --visual-test')
    explicit = getattr(args, 'backend_root', None)
    root = Path(root).resolve()
    if not explicit or Path(explicit).resolve() != root or not (root/'.onyx-acceptance-root').is_file():
        raise ValueError('Backend acceptance requires an explicitly isolated --backend-root with .onyx-acceptance-root marker')
    if protected_root and root == Path(protected_root).resolve():
        raise ValueError('The normal backend installation cannot be an acceptance root')
    config = _contained(root/'config.toml', root)
    if not config.is_file():
        raise ValueError('The isolated backend root needs its own config.toml')
    data = _contained(root/'data', root)
    for name in ('kk.db', 'ambient.db', 'desktop-settings.json', 'preferences.json', 'runtime_control.json'):
        _contained(data/name, root)
    mode = writing[0]
    destination = str(Path(getattr(args, mode)).resolve())
    run_id = sha256((mode+'\0'+destination).encode()).hexdigest()[:16]
    return _contained(data/'client-acceptance'/run_id/'preferences.json', root)
