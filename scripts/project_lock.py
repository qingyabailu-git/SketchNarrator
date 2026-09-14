"""Cooperating project writers share an OS lock; rendering has a separate lease."""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os
import threading
import time

_guard = threading.Lock()
_locks = {}

@contextmanager
def project_lock(root, name="write"):
    path = Path(root).resolve() / (".sketchnarrator-" + name + ".lock")
    with _guard:
        local = _locks.setdefault(str(path), threading.Lock())
    # The thread mutex is needed as well as the process-level file lock.
    with local:
        with path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in (13, 11, 36):
                            raise
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def serialized_project(name="write"):
    def decorate(function):
        @wraps(function)
        def wrapped(root, *args, **kwargs):
            # One physical project may have multiple path spellings (8.3 paths
            # on Windows, /var and /private/var on macOS).  Lock and execute
            # against the same canonical root so identity and containment
            # checks cannot disagree.
            canonical_root = Path(root).expanduser().resolve()
            with project_lock(canonical_root, name):
                return function(canonical_root, *args, **kwargs)
        return wrapped
    return decorate
