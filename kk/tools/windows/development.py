"""Bounded terminal execution behind DANGEROUS permission and cancellation."""
import os
from pathlib import Path
import subprocess
import tempfile
from time import monotonic, sleep


def run_terminal(command, cwd, shell='powershell', timeout=60, environment=None, check=lambda: None):
    directory = Path(cwd)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('cwd must be an existing absolute directory')
    if not 0 < timeout <= 600:
        raise ValueError('timeout must be between 0 and 600 seconds')
    if shell not in ('powershell', 'cmd'):
        raise ValueError('Unsupported shell')
    utf8 = '[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; $OutputEncoding = [Console]::OutputEncoding; '
    argv = (['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', utf8+command]
            if shell == 'powershell' else ['cmd.exe', '/d', '/s', '/c', 'chcp 65001 >nul & '+command])
    return run_process(argv, directory, timeout, environment, check)


def run_process(argv, cwd, timeout=60, environment=None, check=lambda: None):
    import psutil
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    for key, value in (environment or {}).items():
        if not isinstance(key, str) or not isinstance(value, str) or '=' in key or '\0' in key+value:
            raise ValueError('Invalid environment')
        env[key] = value
    check()
    started = monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        timed_out = False
        try:
            while process.poll() is None:
                check()
                if monotonic()-started > timeout:
                    timed_out = True
                    break
                if out.tell()+err.tell() > 8_000_000:
                    raise RuntimeError('Command output exceeded 8 MB limit')
                sleep(.05)
        finally:
            if process.poll() is None:
                try:
                    children = psutil.Process(process.pid).children(recursive=True)
                    for child in reversed(children):
                        try: child.kill()
                        except psutil.NoSuchProcess: pass
                except psutil.NoSuchProcess: pass
                process.kill()
                process.wait(timeout=5)
        out.seek(0); err.seek(0)
        stdout, stderr = out.read(100001), err.read(100001)
        return dict(stdout=stdout[:100000].decode('utf-8', errors='replace'),
            stderr=stderr[:100000].decode('utf-8', errors='replace'), exit_code=process.returncode,
            timed_out=timed_out, truncated=len(stdout)>100000 or len(stderr)>100000,
            cwd=str(cwd), seconds=monotonic()-started)


def git_status(cwd, check=lambda: None):
    path = Path(cwd)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('cwd must be an existing absolute directory')
    return run_process(['git', '--no-optional-locks', '-C', str(path), 'status', '--short', '--branch'], path, 15, check=check)
