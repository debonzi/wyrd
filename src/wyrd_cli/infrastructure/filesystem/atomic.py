"""Linux-local durable publication primitives with fault-injection seams."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

from .paths import is_private_temp

FaultInjector = Callable[[str, Path], None]

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAMEAT2 = getattr(_LIBC, "renameat2", None)
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    _RENAMEAT2.restype = ctypes.c_int


class AtomicOperations:
    """Small injectable syscall boundary used only inside the adapter."""

    def __init__(self, fault_injector: FaultInjector | None = None) -> None:
        self._fault_injector = fault_injector

    def hit(self, point: str, path: Path) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point, path)

    def unique_temp(self, directory: Path) -> Path:
        return directory / f".wyrd-tmp-{uuid.uuid4().hex}.tmp"

    def unique_init_staging(self, root: Path) -> Path:
        return root / f".wyrd-init-{uuid.uuid4().hex}.tmp"

    def write_new_file(self, path: Path, data: bytes, *, mode: int = 0o600) -> None:
        """Create, fully write, flush, and fsync one absent regular file."""

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        self.hit("open", path)
        descriptor = os.open(path, flags, mode)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                self.hit("write", path)
                stream.write(data)
                self.hit("flush", path)
                stream.flush()
                self.hit("file_fsync", path)
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def write_temp(self, directory: Path, data: bytes) -> Path:
        path = self.unique_temp(directory)
        self.write_new_file(path, data)
        return path

    def fsync_directory(self, directory: Path) -> None:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        self.hit("open", directory)
        descriptor = os.open(directory, flags)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise NotADirectoryError(str(directory))
            self.hit("directory_fsync", directory)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def replace_file(self, destination: Path, data: bytes) -> None:
        temporary: Path | None = None
        try:
            temporary = self.write_temp(destination.parent, data)
            self.hit("replace", destination)
            os.replace(temporary, destination)
            temporary = None
            self.fsync_directory(destination.parent)
        finally:
            if temporary is not None:
                self.cleanup_owned_temp(temporary)

    def publish_file_absent(self, destination: Path, data: bytes) -> None:
        temporary: Path | None = None
        try:
            temporary = self.write_temp(destination.parent, data)
            self.hit("link", destination)
            os.link(temporary, destination, follow_symlinks=False)
            self.fsync_directory(destination.parent)
            published_temp = temporary
            temporary = None
            self.cleanup_owned_temp(published_temp)
        finally:
            if temporary is not None:
                self.cleanup_owned_temp(temporary)

    def publish_directory_absent(self, staging: Path, destination: Path) -> None:
        self.hit("rename", destination)
        rename_noreplace(staging, destination)
        self.fsync_directory(destination.parent)

    def cleanup_owned_temp(self, path: Path) -> None:
        """Unlink only the exact private entry created by this operation."""

        if not is_private_temp(path.name):
            raise ValueError(f"refusing to clean non-private path: {path}")
        self.hit("cleanup", path)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def cleanup_owned_tree(self, path: Path) -> None:
        """Remove an exact staging tree without following any symlink in it."""

        self.hit("cleanup", path)
        _remove_tree_nofollow(path)


def rename_noreplace(source: Path, destination: Path) -> None:
    """Linux rename with create-if-absent semantics for files or directories."""

    if _RENAMEAT2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    result = _RENAMEAT2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(destination))


def _remove_tree_nofollow(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
        with os.scandir(path) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            _remove_tree_nofollow(child)
        os.rmdir(path)
    else:
        os.unlink(path)
