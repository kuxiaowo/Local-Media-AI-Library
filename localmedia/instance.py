"""Prevent two app instances from dispatching work against the same library."""
import contextlib
import os


@contextlib.contextmanager
def library_lock(directory, name='instance.lock'):
    stream = open(directory / name, 'a+b')
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b'0');stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('这个图库已在另一个 LocalMedia 进程中打开') from None
    try:
        yield
    finally:
        stream.seek(0)
        if os.name == 'nt':
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()
