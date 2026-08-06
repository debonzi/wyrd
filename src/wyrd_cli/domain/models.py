"""Immutable canonical domain models.

These models represent storage-facing state only. Inverse dependencies, activity,
and summaries are application projections and are deliberately absent here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .values import LABEL_PATTERN

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
_TIMESTAMP_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z", re.ASCII)


class ResourceStatus(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    DISMISSED = "dismissed"

    @property
    def terminal(self) -> bool:
        return self is not ResourceStatus.OPEN


class ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskIdentity(ImmutableModel):
    """Structured task identity; parentage never comes from a storage path."""

    ticket_id: PositiveInt
    number: PositiveInt

    @property
    def public_id(self) -> str:
        return f"{self.ticket_id}.{self.number}"

    def __str__(self) -> str:
        return self.public_id


class Project(ImmutableModel):
    """Project metadata plus the opaque absolute root supplied by storage."""

    schema_version: Literal[1] = 1
    name: str
    created_at: datetime
    root: str

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value: str) -> str:
        if not value or value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("project name must be non-empty, trimmed, and single-line")
        if len(value) > 100:
            raise ValueError("project name must have at most 100 code points")
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def canonical_created_at(cls, value: object) -> datetime:
        return validate_utc_second(value)

    @field_validator("root")
    @classmethod
    def nonempty_root(cls, value: str) -> str:
        if not value:
            raise ValueError("project root must not be empty")
        return value


class Resource(ImmutableModel):
    revision: PositiveInt
    title: str
    status: ResourceStatus
    labels: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    body: str = ""

    @field_validator("title")
    @classmethod
    def canonical_title(cls, value: str) -> str:
        if not value or value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("title must be non-empty, trimmed, and single-line")
        if len(value) > 256:
            raise ValueError("title must have at most 256 code points")
        return value

    @field_validator("labels")
    @classmethod
    def canonical_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(LABEL_PATTERN.fullmatch(label) is None for label in value):
            raise ValueError("resource has an invalid label")
        if tuple(sorted(set(value))) != value:
            raise ValueError("labels must be unique and sorted")
        return value

    @field_validator("created_at", "updated_at", "closed_at", mode="before")
    @classmethod
    def canonical_timestamp(cls, value: object) -> datetime | None:
        if value is None:
            return None
        return validate_utc_second(value)

    @field_validator("body")
    @classmethod
    def canonical_body(cls, value: str) -> str:
        if "\r" in value:
            raise ValueError("body line endings must be LF")
        return value

    @model_validator(mode="after")
    def coherent_lifecycle(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.status is ResourceStatus.OPEN:
            if self.closed_at is not None:
                raise ValueError("open resources must not have closed_at")
        elif self.closed_at is None:
            raise ValueError("terminal resources must have closed_at")
        if self.closed_at is not None and self.closed_at < self.created_at:
            raise ValueError("closed_at cannot precede created_at")
        return self


class Ticket(Resource):
    id: PositiveInt
    blocked_by: tuple[PositiveInt, ...] = ()

    @field_validator("blocked_by")
    @classmethod
    def canonical_blockers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("blocked_by must be unique and sorted")
        return value


class Task(Resource):
    identity: TaskIdentity
    blocked_by: tuple[PositiveInt, ...] = ()

    @field_validator("blocked_by")
    @classmethod
    def canonical_blockers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("blocked_by must be unique and sorted")
        return value

    @property
    def ticket_id(self) -> int:
        return self.identity.ticket_id

    @property
    def number(self) -> int:
        return self.identity.number

    @property
    def public_id(self) -> str:
        return self.identity.public_id


ResourceIdentity = int | TaskIdentity


def validate_utc_second(value: object) -> datetime:
    """Validate canonical RFC 3339 UTC timestamps or UTC datetime values."""

    if isinstance(value, str):
        if _TIMESTAMP_PATTERN.fullmatch(value) is None:
            raise ValueError("timestamp must use RFC 3339 UTC second precision with Z")
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError as error:
            raise ValueError("timestamp is not a valid UTC instant") from error
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime or canonical string")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
    if value.microsecond:
        raise ValueError("timestamp must have second precision")
    return value.astimezone(UTC)


def replace_ticket(ticket: Ticket, **changes: object) -> Ticket:
    values = {
        "id": ticket.id,
        "revision": ticket.revision,
        "title": ticket.title,
        "status": ticket.status,
        "labels": ticket.labels,
        "blocked_by": ticket.blocked_by,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "closed_at": ticket.closed_at,
        "body": ticket.body,
    }
    values.update(changes)
    return Ticket.model_validate(values)


def replace_task(task: Task, **changes: object) -> Task:
    values = {
        "identity": task.identity,
        "revision": task.revision,
        "title": task.title,
        "status": task.status,
        "labels": task.labels,
        "blocked_by": task.blocked_by,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "closed_at": task.closed_at,
        "body": task.body,
    }
    values.update(changes)
    return Task.model_validate(values)
