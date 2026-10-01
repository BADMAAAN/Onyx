"""OS-owned lock: a crashed process cannot leave a stale PID lock."""
import msvcrt


class SingleInstance:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open('a+b')
        try:
            if path.stat().st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise RuntimeError('Onyx is already running for this repository') from None

    def close(self):
        if not self.file.closed:
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
