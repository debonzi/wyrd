"""Resource identity parsing and input value normalization."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .errors import DomainValidationError, IdentitySyntaxError, InvalidLabelError

TICKET_ID_PATTERN = re.compile(r"[1-9][0-9]*\Z", re.ASCII)
TASK_ID_PATTERN = re.compile(r"([1-9][0-9]*)\.([1-9][0-9]*)\Z", re.ASCII)
LABEL_PATTERN = re.compile(r"[a-z0-9][a-z0-9:_]{0,19}\Z", re.ASCII)


def parse_ticket_id(value: str) -> int:
    """Parse exact public ticket syntax without signs, padding, or whitespace."""

    if not isinstance(value, str) or TICKET_ID_PATTERN.fullmatch(value) is None:
        raise IdentitySyntaxError(
            f"'{value}' is not a valid ticket ID.", {"value": value}
        )
    return int(value)


def parse_task_id(value: str):
    """Parse exact ``<ticket-id>.<task-number>`` syntax."""

    from .models import TaskIdentity

    match = TASK_ID_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise IdentitySyntaxError(
            f"'{value}' is not a valid task ID.", {"value": value}
        )
    return TaskIdentity(ticket_id=int(match.group(1)), number=int(match.group(2)))


def format_task_id(ticket_id: int, number: int) -> str:
    """Format a structured, positive task identity for public use."""

    from .models import TaskIdentity

    return TaskIdentity(ticket_id=ticket_id, number=number).public_id


def validate_project_name(value: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("Project name must be text.")
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError("Project name must not be empty.")
    if "\n" in normalized or "\r" in normalized:
        raise DomainValidationError("Project name must be a single line.")
    if len(normalized) > 100:
        raise DomainValidationError(
            "Project name must be at most 100 Unicode code points."
        )
    return normalized


def validate_title(value: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("Title must be text.")
    normalized = value.strip()
    if not normalized:
        raise DomainValidationError("Title must not be empty.")
    if "\n" in normalized or "\r" in normalized:
        raise DomainValidationError("Title must be a single line.")
    if len(normalized) > 256:
        raise DomainValidationError("Title must be at most 256 Unicode code points.")
    return normalized


def validate_label(value: str) -> str:
    if not isinstance(value, str) or LABEL_PATTERN.fullmatch(value) is None:
        raise InvalidLabelError(value)
    return value


def normalize_labels(values: Iterable[str]) -> tuple[str, ...]:
    """Validate, de-duplicate, and ASCII-lexicographically order labels."""

    return tuple(sorted({validate_label(value) for value in values}))


def normalize_body(value: str) -> str:
    if not isinstance(value, str):
        raise DomainValidationError("Body must be text.")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def normalize_search_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError("Text filter must not be empty.")
    return unicodedata.normalize("NFC", value.strip()).casefold()


def searchable_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def validate_positive_int(value: int, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DomainValidationError(
            f"{field_name} must be a positive integer.", {"field": field_name}
        )
    return value
