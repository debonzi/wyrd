"""CLI-only parsing of option presence, numbers, and body sources."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import typer

from wyrd_cli.application.dto import UNSET, CreateResourceRequest, EditResourceRequest

from .boundary import PresentationError


_ALLOWED_STATUSES = {"open", "completed", "dismissed", "all"}


def validate_lock_timeout(value: float) -> float:
    if value < 0 or not math.isfinite(value):
        raise typer.BadParameter("must be a finite non-negative number")
    return value


def validate_positive_option(value: int | None) -> int | None:
    if value is not None and value < 1:
        raise typer.BadParameter("must be a positive integer")
    return value


def validate_status(value: str) -> str:
    if value not in _ALLOWED_STATUSES:
        raise typer.BadParameter("must be open, completed, dismissed, or all")
    return value


def create_request(
    *,
    title: str,
    body: str | None,
    body_file: str | None,
    labels: list[str],
    binary_stdin: Callable[[], BinaryIO],
) -> CreateResourceRequest:
    return CreateResourceRequest(
        title=title,
        body=read_body(
            body=body,
            body_file=body_file,
            omitted_default="",
            binary_stdin=binary_stdin,
        ),
        labels=tuple(labels),
    )


def edit_request(
    *,
    title: str | None,
    body: str | None,
    body_file: str | None,
    add_labels: list[str],
    remove_labels: list[str],
    expected_revision: int | None,
    binary_stdin: Callable[[], BinaryIO],
) -> EditResourceRequest:
    if (
        title is None
        and body is None
        and body_file is None
        and not add_labels
        and not remove_labels
    ):
        raise PresentationError(
            "usage_error",
            "Edit requires at least one explicit title, body, add-label, or remove-label option.",
            {},
            exit_code=2,
        )
    added = tuple(dict.fromkeys(add_labels))
    removed = tuple(dict.fromkeys(remove_labels))
    overlap = sorted(set(added) & set(removed))
    if overlap:
        raise PresentationError(
            "usage_error",
            "The same label cannot be added and removed in one edit.",
            {"labels": overlap},
            exit_code=2,
        )
    selected_body = read_body(
        body=body,
        body_file=body_file,
        omitted_default=UNSET,
        binary_stdin=binary_stdin,
    )
    return EditResourceRequest(
        title=UNSET if title is None else title,
        body=selected_body,
        add_labels=added,
        remove_labels=removed,
        expected_revision=expected_revision,
    )


def read_body(
    *,
    body: str | None,
    body_file: str | None,
    omitted_default: str | object,
    binary_stdin: Callable[[], BinaryIO],
) -> str | object:
    if body is not None and body_file is not None:
        raise PresentationError(
            "usage_error",
            "--body and --body-file are mutually exclusive.",
            {},
            exit_code=2,
        )
    if body is not None:
        return body
    if body_file is None:
        return omitted_default

    try:
        data = binary_stdin().read() if body_file == "-" else Path(body_file).read_bytes()
    except OSError as error:
        raise PresentationError(
            "validation_error",
            f"Body file '{body_file}' could not be read.",
            {"path": body_file},
        ) from error
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PresentationError(
            "validation_error",
            f"Body input from '{body_file}' is not valid UTF-8.",
            {"path": body_file, "offset": error.start},
        ) from error
