from pathlib import Path
import fnmatch
import os
import shutil
import tempfile
from .files import read_text_file


def absolute(path):
    target = Path(path)
    if not target.is_absolute():
        raise ValueError('Use an absolute Windows path')
    target = target.resolve()
    forbidden = {'.ssh', '.aws', '.azure', 'credentials', 'credentials.json', 'auth.json',
                 'tokens.json', 'cookies', 'login data', 'key4.db', 'logins.json', 'wallet.dat'}
    if (set(p.casefold() for p in target.parts) & forbidden or target.suffix.casefold() in {'.pem', '.key', '.pfx'}
            or target.name.casefold().startswith(('.env', 'id_rsa', 'id_ed25519'))):
        raise PermissionError('Credential stores are excluded')
    return target


def info(path):
    p = absolute(path)
    stat = p.stat()
    return dict(path=str(p), directory=p.is_dir(), size=stat.st_size, modified=stat.st_mtime)


def search(path, pattern='*', limit=100, check=lambda: None):
    root = absolute(path)
    if not root.is_dir(): raise NotADirectoryError(root)
    output = []
    scanned = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        check()
        dirs[:] = [d for d in dirs if d not in ('.git', '.venv', 'node_modules') and not Path(base, d).is_symlink()]
        for name in files:
            scanned += 1
            if scanned > 100000: return dict(files=output, truncated=True)
            if fnmatch.fnmatch(name.casefold(), pattern.casefold()):
                try: output.append(info(str(Path(base)/name)))
                except (PermissionError, OSError): continue
                if len(output) >= min(max(1, limit), 500):
                    return dict(files=output, truncated=True)
    return dict(files=output, truncated=False)


def write(path, content, overwrite=False):
    p = absolute(path)
    if len(content.encode('utf-8')) > 1_000_000: raise ValueError('Maximum 1 MB')
    if not overwrite:
        with p.open('x', encoding='utf-8', newline='') as stream: stream.write(content)
    else:
        with tempfile.NamedTemporaryFile(dir=p.parent, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(content.encode('utf-8'))
        try: temp.replace(p)
        finally: temp.unlink(missing_ok=True)
    return info(str(p))


def replace(path, old, new, expected_count=1):
    p = absolute(path)
    content = read_text_file(str(p))['content']
    if not old or content.count(old) != expected_count:
        raise ValueError('Replacement count differs; read the file again')
    return write(str(p), content.replace(old, new), True)


def create_directory(path):
    p = absolute(path); p.mkdir(parents=True, exist_ok=False)
    return info(str(p))


def transfer(source, destination, move=False):
    src, dst = absolute(source), absolute(destination)
    if src == Path(src.anchor): raise ValueError('Cannot transfer a drive root')
    if dst.exists(): raise FileExistsError(dst)
    if src == dst or src in dst.parents: raise ValueError('Destination lies inside source')
    if move: shutil.move(str(src), str(dst))
    elif src.is_dir(): shutil.copytree(src, dst, symlinks=True)
    else: shutil.copy2(src, dst)
    return info(str(dst))


def delete(path):
    from send2trash import send2trash
    p = absolute(path)
    if p == Path(p.anchor): raise ValueError('Cannot delete a drive root')
    send2trash(str(p))
    return dict(path=str(p), recycled=True, exists=p.exists())


def open_path(path):
    p = absolute(path)
    if not p.exists(): raise FileNotFoundError(p)
    os.startfile(str(p))
    return dict(path=str(p), launch_requested=True)

