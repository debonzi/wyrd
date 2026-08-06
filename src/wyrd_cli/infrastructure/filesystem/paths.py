"""Canonical filesystem names and no-follow path validation."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from wyrd_cli.domain.errors import CorruptDataError, InvalidProjectError

PRIVATE_TEMP_PATTERN = re.compile(r"\.wyrd-tmp-[0-9a-f]{32}\.tmp\Z", re.ASCII)
INIT_STAGING_PATTERN = re.compile(r"\.wyrd-init-[0-9a-f]{32}\.tmp\Z", re.ASCII)
POSITIVE_DECIMAL_PATTERN = re.compile(r"[1-9][0-9]*\Z", re.ASCII)
TASK_FILE_PATTERN = re.compile(r"([1-9][0-9]*)\.md\Z", re.ASCII)


def is_private_temp(name: str) -> bool:
    """Return whether an entry unambiguously belongs to Wyrd's temp policy."""

    return PRIVATE_TEMP_PATTERN.fullmatch(name) is not None


def is_init_staging(name: str) -> bool:
    return INIT_STAGING_PATTERN.fullmatch(name) is not None


def relative_to_wyrd(path: Path, wyrd_dir: Path) -> str:
    return path.relative_to(wyrd_dir).as_posix()


def lstat_kind(path: Path) -> str:
    """Describe an entry without following it."""

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def require_project_directory(path: Path, *, label: str) -> None:
    kind = lstat_kind(path)
    if kind != "directory":
        raise InvalidProjectError(
            f"The {label} must be a real directory, not {kind}.",
            {"path": str(path), "actual_type": kind},
        )


def require_base_file(path: Path, *, label: str) -> None:
    kind = lstat_kind(path)
    if kind != "file":
        raise InvalidProjectError(
            f"The {label} must be a regular file, not {kind}.",
            {"path": str(path), "actual_type": kind},
        )


def require_canonical_directory(path: Path, *, relative_path: str) -> None:
    kind = lstat_kind(path)
    if kind != "directory":
        raise CorruptDataError(
            f"Canonical path '{relative_path}' must be a directory, not {kind}.",
            {"path": relative_path, "actual_type": kind},
        )


def require_canonical_file(path: Path, *, relative_path: str) -> None:
    kind = lstat_kind(path)
    if kind != "file":
        raise CorruptDataError(
            f"Canonical path '{relative_path}' must be a regular file, not {kind}.",
            {"path": relative_path, "actual_type": kind},
        )


def read_bytes_nofollow(path: Path, *, invalid_project: bool = False) -> bytes:
    """Read a regular file with Linux no-follow protection on the final component."""

    flags = os.O_RDONLY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        error_type = InvalidProjectError if invalid_project else CorruptDataError
        raise error_type(
            f"Could not safely open '{path.name}'.",
            {"path": str(path)},
            cause=error,
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            error_type = InvalidProjectError if invalid_project else CorruptDataError
            raise error_type(
                f"Path '{path.name}' is not a regular file.",
                {"path": str(path), "actual_type": "other"},
            )
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)
