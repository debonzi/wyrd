"""Application-facing persistence and transaction contract.

The vocabulary is semantic: paths, YAML, locks, temporary files, and file
descriptors are intentionally absent. A filesystem adapter can satisfy these
protocols without leaking its representation into core/application code.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from wyrd_cli.application.dto import DoctorProblemDTO
from wyrd_cli.domain.models import Project, Task, TaskIdentity, Ticket


@dataclass(frozen=True)
class LocatedTicket:
    """A safely decoded ticket and its opaque doctor report location."""

    path: str
    ticket: Ticket


@dataclass(frozen=True)
class LocatedTask:
    """A safely decoded task and its opaque doctor report location."""

    path: str
    task: Task


@dataclass(frozen=True)
class StructuralScan:
    """Tolerant structural scan returned only by the doctor boundary.

    Records that cannot be decoded safely are represented by problems rather
    than partial domain objects. Independent records should still be returned.
    """

    project: Project | None
    tickets: tuple[LocatedTicket, ...] = ()
    tasks: tuple[LocatedTask, ...] = ()
    problems: tuple[DoctorProblemDTO, ...] = ()


class ReadTransaction(Protocol):
    """One consistent read snapshot held until context-manager exit.

    Missing resources are returned as ``None``. Corrupt records must raise
    ``CorruptDataError`` rather than masquerading as missing. Discovery,
    invalid-project, unsupported-schema, and transaction failures retain their
    specific application error classes.
    """

    def load_project(self) -> Project:
        """Load project metadata from this transaction's snapshot."""
        ...

    def list_tickets(self) -> tuple[Ticket, ...]:
        """Return all canonical tickets in this snapshot."""
        ...

    def get_ticket(self, ticket_id: int) -> Ticket | None:
        """Return a ticket from this snapshot, or ``None`` when absent."""
        ...

    def list_tasks(self, ticket_id: int) -> tuple[Task, ...]:
        """Return all tasks canonically belonging to one ticket."""
        ...

    def get_task(self, identity: TaskIdentity) -> Task | None:
        """Return a structured task identity from this snapshot when present."""
        ...

    def structural_scan(self) -> StructuralScan:
        """Tolerantly scan safely discoverable records for ``doctor`` only."""
        ...


class WriteTransaction(ReadTransaction, Protocol):
    """Exclusive transaction with create-if-absent and revision-aware writes.

    All ID allocation performed by the application occurs after entry, using
    this same transaction's lists. Creates must never overwrite an existing
    identity. Updates compare ``expected_revision`` with the currently stored
    resource and atomically replace exactly that canonical resource. A clean
    context-manager exit commits; an exceptional exit publishes no staged
    mutation.
    """

    def create_ticket(self, ticket: Ticket) -> None:
        """Publish one complete ticket aggregate iff its ID is absent."""
        ...

    def update_ticket(self, ticket: Ticket, *, expected_revision: int) -> None:
        """Replace one ticket iff its stored revision matches expectation."""
        ...

    def create_task(self, task: Task) -> None:
        """Publish one task iff its structured identity is absent."""
        ...

    def update_task(self, task: Task, *, expected_revision: int) -> None:
        """Replace one task iff its stored revision matches expectation."""
        ...


class StoragePort(Protocol):
    """Storage capabilities consumed by Wyrd application services.

    A port instance is scoped to at most one operational project. A successful
    ``discover`` or ``initialize`` binds that instance for later ``read`` and
    ``write`` calls; composition may instead supply an already-bound instance.
    Implementations classify failures with errors from ``domain.errors``:
    project absence, invalid project, unsupported schema, corrupt data,
    create/revision conflict, lock timeout, and transaction error remain
    distinguishable. They must not turn corruption into a not-found result.
    """

    def suggested_project_name(self, root: str) -> str:
        """Derive the target-directory basename while treating ``root`` opaquely upstream."""
        ...

    def discover(self, start: str) -> Project:
        """Discover, validate, and bind the unique project associated with ``start``."""
        ...

    def initialize(
        self, *, root: str, name: str, created_at: datetime
    ) -> Project:
        """Atomically create or idempotently return and bind a project.

        Concurrent publication, ancestor/nested checks, staging, and
        same-name idempotency belong to the adapter. A different existing name
        raises ``ConflictError`` and invalid/partial state is never replaced.
        """
        ...

    def read(
        self, *, timeout: float = 10.0
    ) -> AbstractContextManager[ReadTransaction]:
        """Open one shared, consistent transaction or raise a typed lock error."""
        ...

    def write(
        self, *, timeout: float = 10.0
    ) -> AbstractContextManager[WriteTransaction]:
        """Open one exclusive transaction or raise a typed lock error."""
        ...
