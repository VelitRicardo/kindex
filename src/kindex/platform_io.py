"""Cross-platform file primitives: no-follow opens and exclusive advisory locks.

POSIX keeps ``O_NOFOLLOW`` and ``flock``. Windows has neither, so:

* ``open_nofollow`` refuses a symlink or other reparse point (junction) at the
  final component, checked before opening and again against the opened handle,
  so a link swapped in between the two checks is refused too. The error is the
  same ``ELOOP`` that ``O_NOFOLLOW`` raises on Linux.
* ``lock`` takes a one-byte ``msvcrt`` lock far past any data the file holds.
  Windows byte-range locks are mandatory; locking byte 0 would block ordinary
  reads and writes of a lock file that also carries data.

Like ``flock``, the lock is per open descriptor, exclusive, and released when
the descriptor is closed or the process exits.

``fsync_directory`` makes a rename durable on POSIX. Windows cannot open a
directory with ``os.open``; NTFS journals the rename itself, so it is a no-op.
"""

from __future__ import annotations

import errno
import os
import stat
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

__all__ = ["open_nofollow", "lock", "unlock", "fsync_directory"]

_LOCK_OFFSET = 0x7FFFFFFF  # one byte past any realistic lock-file payload
_RETRY_SECONDS = 0.01


def _is_link(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _refuse(path) -> OSError:
    return OSError(errno.ELOOP, "Refusing to follow a symbolic link", str(path))


def open_nofollow(path, flags: int, mode: int = 0o777) -> int:
    """``os.open(path, flags | O_NOFOLLOW, mode)`` that also refuses links on Windows."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        return os.open(path, flags | nofollow, mode)
    try:
        if _is_link(os.lstat(path)):
            raise _refuse(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, flags | getattr(os, "O_BINARY", 0), mode)
    try:
        current = os.lstat(path)
        if _is_link(current) or not os.path.samestat(current, os.fstat(fd)):
            raise _refuse(path)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _locking(fd: int, mode: int) -> None:
    position = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
    try:
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def lock(fd: int, *, blocking: bool = True) -> None:
    """Exclusive lock on ``fd``. Non-blocking raises ``BlockingIOError`` while another holder has it."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    while True:
        try:
            _locking(fd, msvcrt.LK_NBLCK)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EDEADLK, errno.EAGAIN):
                raise
            if not blocking:
                raise BlockingIOError(errno.EAGAIN, "Lock is held by another descriptor") from None
            time.sleep(_RETRY_SECONDS)


def unlock(fd: int) -> None:
    """Release a lock taken with ``lock``. Closing the descriptor also releases it."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    _locking(fd, msvcrt.LK_UNLCK)


def fsync_directory(path) -> None:
    """Flush a directory entry (e.g. after ``os.replace``) where the platform supports it."""
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
