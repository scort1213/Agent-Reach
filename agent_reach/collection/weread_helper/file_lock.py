# SPDX-License-Identifier: MIT
"""Standalone, standard-library process lock for collection state files.

Keep the lock file after release: deleting it can let competing processes lock
different files at the same path. Closing the handle also releases a lock when
a process exits unexpectedly.
"""

import errno
import importlib
import os
import time
from contextlib import contextmanager


def _windows_acquire(handle):
    msvcrt = importlib.import_module("msvcrt")

    # LK_LOCK gives up after ten seconds. Collection/transcription can take
    # longer, so retain flock's blocking behavior with nonblocking attempts.
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            time.sleep(0.1)


@contextmanager
def exclusive_lock(path):
    """Hold the same one-byte/whole-file exclusive lock until the body exits."""
    with open(path, "a+b") as handle:
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")

            _windows_acquire(handle)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
