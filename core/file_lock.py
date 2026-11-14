"""Exclusive local-file locks for catalog and processing workers on POSIX/Windows.

Closing the handle releases the lock. POSIX flock retains its descriptor
inheritance semantics for the existing candidate training subprocess.
"""
import errno
import os
import time

WINDOWS = os.name == 'nt'


def lock_exclusive(handle, *, blocking=True):
    if not WINDOWS:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    import msvcrt
    handle.seek(0)
    if not handle.read(1):
        handle.write(b'0' if 'b' in handle.mode else '0')
        handle.flush()
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            if not blocking:
                raise BlockingIOError(exc.errno, 'File lock is held by another process') from exc
            time.sleep(.05)


def unlock(handle):
    if WINDOWS:
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
