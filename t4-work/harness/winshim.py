"""Windows-only shim so the official scorer's POSIX directory-fd reads run locally.

`qfbench2_track_analysis.corpus` opens the corpus directory with O_DIRECTORY and then each file
with dir_fd=, neither of which exists on Windows. This emulates exactly that pattern (and opens
files in binary mode) without changing what is read. It is a no-op on POSIX.
"""
import os

if not hasattr(os, "O_DIRECTORY"):
    _real_open, _real_close = os.open, os.close
    _fake: dict[int, str] = {}
    os.O_DIRECTORY = 0x40000000

    def _open(path, flags, mode=0o777, *, dir_fd=None):
        if flags & os.O_DIRECTORY:
            if not os.path.isdir(path):
                raise NotADirectoryError(path)
            fd = -1000 - len(_fake)
            _fake[fd] = os.fspath(path)
            return fd
        if dir_fd is not None:
            path = os.path.join(_fake[dir_fd], path)
        return _real_open(path, (flags & ~os.O_DIRECTORY) | getattr(os, "O_BINARY", 0), mode)

    def _close(fd):
        if fd in _fake:
            _fake.pop(fd)
            return None
        return _real_close(fd)

    os.open, os.close = _open, _close
