"""Persistent Linux flock acquisition with monotonic timeout handling."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from wyrd_cli.domain.errors import InvalidProjectError, LockTimeoutError, StorageTransactionError


@contextmanager
def project_lock(
    path: Path,
    *,
    exclusive: bool,
    timeout: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = 0.01,
) -> Iterator[int]:
    """Open an existing empty regular lock without following and acquire flock."""

    flags = (os.O_RDWR if exclusive else os.O_RDONLY) | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise InvalidProjectError(
            "The persistent project lock is missing or unsafe.",
            {"path": "lock"},
            cause=error,
        ) from error
    acquired = False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != 0:
            raise InvalidProjectError(
                "The persistent project lock must be an empty regular file.",
                {"path": "lock", "size": metadata.st_size},
            )
        try:
            path_metadata = path.lstat()
        except OSError as error:
            raise InvalidProjectError(
                "The persistent project lock changed while being opened.",
                {"path": "lock"},
                cause=error,
            ) from error
        if (
            not stat.S_ISREG(path_metadata.st_mode)
            or path_metadata.st_dev != metadata.st_dev
            or path_metadata.st_ino != metadata.st_ino
        ):
            raise InvalidProjectError(
                "The persistent project lock is unsafe.", {"path": "lock"}
            )

        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as error:
                if error.errno == errno.EINTR:
                    continue
                if error.errno not in (errno.EACCES, errno.EAGAIN):
                    raise StorageTransactionError(
                        "Could not acquire the project lock.",
                        {"exclusive": exclusive},
                        cause=error,
                    ) from error
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise LockTimeoutError(
                        "Timed out while waiting for the project lock.",
                        {"timeout": timeout, "exclusive": exclusive},
                    ) from error
                sleep(min(poll_interval, remaining))
        yield descriptor
    finally:
        if acquired:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)
