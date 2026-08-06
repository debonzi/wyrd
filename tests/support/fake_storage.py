"""Transactional in-memory test fake; not a production storage adapter."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from wyrd_cli.application.dto import DoctorProblemDTO
from wyrd_cli.application.storage import (
    LocatedTask,
    LocatedTicket,
    ReadTransaction,
    StructuralScan,
    WriteTransaction,
)
from wyrd_cli.domain.errors import ConflictError, StorageConflictError
from wyrd_cli.domain.models import Project, Task, TaskIdentity, Ticket

DEFAULT_TIME = datetime(2026, 8, 5, 18, 59, 36, tzinfo=UTC)


class FixedClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values or (DEFAULT_TIME,))
        self.calls = 0

    def now(self) -> datetime:
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


class FakeStorage:
    """Snapshotting fake with staged writes and call recording."""

    def __init__(
        self,
        *,
        project: Project | None = None,
        tickets: tuple[Ticket, ...] = (),
        tasks: tuple[Task, ...] = (),
    ) -> None:
        self.project = project or Project(
            name="Example project",
            root="/project",
            created_at=DEFAULT_TIME,
        )
        self.tickets = {ticket.id: ticket for ticket in tickets}
        self.tasks = {
            ticket_id: {} for ticket_id in self.tickets
        }
        for task in tasks:
            self.tasks.setdefault(task.ticket_id, {})[task.number] = task
        self.calls: list[str] = []
        self.writes: list[tuple[str, int | str]] = []
        self.read_transactions = 0
        self.write_transactions = 0
        self.injected_errors: dict[str, BaseException] = {}
        self.scan_override: StructuralScan | None = None

    def suggested_project_name(self, root: str) -> str:
        self.calls.append("suggested_project_name")
        return root.rstrip("/").rsplit("/", 1)[-1]

    def discover(self, start: str) -> Project:
        self.calls.append("discover")
        self._raise_if_injected("discover")
        return self.project

    def discover_for_diagnostics(self, start: str) -> None:
        self.calls.append("discover_for_diagnostics")
        self._raise_if_injected("discover_for_diagnostics")

    def initialize(
        self,
        *,
        root: str,
        name: str,
        created_at: datetime,
        name_was_explicit: bool = True,
    ) -> Project:
        self.calls.append("initialize")
        self._raise_if_injected("initialize")
        if self.project.root == root:
            if name_was_explicit and self.project.name != name:
                raise ConflictError(
                    "A project with a different name already exists.",
                    {"existing_name": self.project.name, "requested_name": name},
                )
            return self.project
        self.project = Project(root=root, name=name, created_at=created_at)
        self.tickets = {}
        self.tasks = {}
        return self.project

    @contextmanager
    def read(self, *, timeout: float = 10.0) -> Iterator[ReadTransaction]:
        self.calls.append(f"read.enter:{timeout}")
        self.read_transactions += 1
        self._raise_if_injected("read")
        transaction = _FakeTransaction(
            self,
            dict(self.tickets),
            {ticket_id: dict(tasks) for ticket_id, tasks in self.tasks.items()},
            writable=False,
        )
        try:
            yield transaction
        finally:
            self.calls.append("read.exit")

    @contextmanager
    def write(self, *, timeout: float = 10.0) -> Iterator[WriteTransaction]:
        self.calls.append(f"write.enter:{timeout}")
        self.write_transactions += 1
        self._raise_if_injected("write")
        transaction = _FakeTransaction(
            self,
            dict(self.tickets),
            {ticket_id: dict(tasks) for ticket_id, tasks in self.tasks.items()},
            writable=True,
        )
        try:
            yield transaction
        except BaseException:
            self.calls.append("write.rollback")
            raise
        else:
            self.tickets = transaction.tickets
            self.tasks = transaction.tasks
            self.calls.append("write.commit")

    def _raise_if_injected(self, operation: str) -> None:
        if error := self.injected_errors.get(operation):
            raise error


class _FakeTransaction:
    def __init__(
        self,
        storage: FakeStorage,
        tickets: dict[int, Ticket],
        tasks: dict[int, dict[int, Task]],
        *,
        writable: bool,
    ) -> None:
        self.storage = storage
        self.tickets = tickets
        self.tasks = tasks
        self.writable = writable

    def load_project(self) -> Project:
        self.storage.calls.append("load_project")
        self.storage._raise_if_injected("load_project")
        return self.storage.project

    def list_tickets(self) -> tuple[Ticket, ...]:
        self.storage.calls.append("list_tickets")
        self.storage._raise_if_injected("list_tickets")
        return tuple(self.tickets.values())

    def get_ticket(self, ticket_id: int) -> Ticket | None:
        self.storage.calls.append(f"get_ticket:{ticket_id}")
        return self.tickets.get(ticket_id)

    def list_tasks(self, ticket_id: int) -> tuple[Task, ...]:
        self.storage.calls.append(f"list_tasks:{ticket_id}")
        self.storage._raise_if_injected("list_tasks")
        return tuple(self.tasks.get(ticket_id, {}).values())

    def get_task(self, identity: TaskIdentity) -> Task | None:
        self.storage.calls.append(f"get_task:{identity.public_id}")
        return self.tasks.get(identity.ticket_id, {}).get(identity.number)

    def structural_scan(self) -> StructuralScan:
        self.storage.calls.append("structural_scan")
        if self.storage.scan_override is not None:
            return self.storage.scan_override
        return StructuralScan(
            project=self.storage.project,
            tickets=tuple(
                LocatedTicket(path=f"tickets/{ticket.id}/ticket.md", ticket=ticket)
                for ticket in self.tickets.values()
            ),
            tasks=tuple(
                LocatedTask(
                    path=f"tickets/{task.ticket_id}/tasks/{task.number}.md",
                    task=task,
                )
                for siblings in self.tasks.values()
                for task in siblings.values()
            ),
            known_ticket_ids=tuple(self.tickets),
            known_task_identities=tuple(
                task.identity
                for siblings in self.tasks.values()
                for task in siblings.values()
            ),
        )

    def create_ticket(self, ticket: Ticket) -> None:
        self._ensure_writable()
        self.storage._raise_if_injected("create_ticket")
        if ticket.id in self.tickets:
            raise StorageConflictError("Ticket already exists.")
        self.tickets[ticket.id] = ticket
        self.tasks[ticket.id] = {}
        self.storage.writes.append(("create_ticket", ticket.id))

    def update_ticket(self, ticket: Ticket, *, expected_revision: int) -> None:
        self._ensure_writable()
        self.storage._raise_if_injected("update_ticket")
        current = self.tickets.get(ticket.id)
        if current is None or current.revision != expected_revision:
            raise StorageConflictError("Ticket revision changed.")
        self.tickets[ticket.id] = ticket
        self.storage.writes.append(("update_ticket", ticket.id))

    def create_task(self, task: Task) -> None:
        self._ensure_writable()
        self.storage._raise_if_injected("create_task")
        siblings = self.tasks.setdefault(task.ticket_id, {})
        if task.number in siblings:
            raise StorageConflictError("Task already exists.")
        siblings[task.number] = task
        self.storage.writes.append(("create_task", task.public_id))

    def update_task(self, task: Task, *, expected_revision: int) -> None:
        self._ensure_writable()
        self.storage._raise_if_injected("update_task")
        current = self.tasks.get(task.ticket_id, {}).get(task.number)
        if current is None or current.revision != expected_revision:
            raise StorageConflictError("Task revision changed.")
        self.tasks[task.ticket_id][task.number] = task
        self.storage.writes.append(("update_task", task.public_id))

    def _ensure_writable(self) -> None:
        if not self.writable:
            raise AssertionError("write attempted in read transaction")


def problem(path: str, code: str) -> DoctorProblemDTO:
    return DoctorProblemDTO(path=path, code=code, message=code, details={})
