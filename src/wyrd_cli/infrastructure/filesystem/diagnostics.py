"""Tolerant, read-only structural scan for the doctor use case."""

from __future__ import annotations

import os
from pathlib import Path

from wyrd_cli.application.dto import DoctorProblemDTO
from wyrd_cli.application.storage import LocatedTask, LocatedTicket, StructuralScan
from wyrd_cli.domain.errors import ApplicationError, UnsupportedSchemaError
from wyrd_cli.domain.models import TaskIdentity

from .codec import CodecFailure, decode_project, decode_task, decode_ticket
from .paths import (
    POSITIVE_DECIMAL_PATTERN,
    TASK_FILE_PATTERN,
    is_private_temp,
    lstat_kind,
    read_bytes_nofollow,
)


class StructuralScanner:
    """Scan independent records without allowing one malformed entry to abort all."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.wyrd = root / ".wyrd"
        self.problems: list[DoctorProblemDTO] = []
        self.tickets: list[LocatedTicket] = []
        self.tasks: list[LocatedTask] = []
        self.known_ticket_ids: list[int] = []
        self.known_task_identities: list[TaskIdentity] = []

    def scan(self) -> StructuralScan:
        project = self._scan_project()
        self._scan_top_level()
        self._scan_tickets()
        return StructuralScan(
            project=project,
            tickets=tuple(self.tickets),
            tasks=tuple(self.tasks),
            problems=tuple(self.problems),
            known_ticket_ids=tuple(self.known_ticket_ids),
            known_task_identities=tuple(self.known_task_identities),
        )

    def _scan_project(self):
        path = self.wyrd / "project.yaml"
        if not self._require_file(path, "project.yaml"):
            return None
        try:
            return decode_project(read_bytes_nofollow(path), root=str(self.root))
        except UnsupportedSchemaError as error:
            self._application_problem("project.yaml", error)
        except CodecFailure as error:
            self._codec_problem("project.yaml", error)
        except ApplicationError as error:
            self._application_problem("project.yaml", error)
        return None

    def _scan_top_level(self) -> None:
        entries = self._entries(self.wyrd, ".")
        if entries is None:
            return
        expected = {"project.yaml", "lock", "tickets"}
        for name, path in entries.items():
            if name in expected:
                continue
            if is_private_temp(name):
                self._problem(
                    name,
                    "stale_temporary",
                    "A private Wyrd temporary entry remains.",
                    {"entry": name},
                )
            else:
                self._problem(
                    name,
                    "unexpected_path",
                    "An unexpected managed path exists.",
                    {"entry": name},
                )

    def _scan_tickets(self) -> None:
        tickets_dir = self.wyrd / "tickets"
        if not self._require_directory(tickets_dir, "tickets"):
            return
        entries = self._entries(tickets_dir, "tickets")
        if entries is None:
            return
        for name in sorted(entries, key=_entry_sort_key):
            path = entries[name]
            relative = f"tickets/{name}"
            if is_private_temp(name):
                self._problem(
                    relative,
                    "stale_temporary",
                    "A private Wyrd temporary entry remains.",
                    {"entry": name},
                )
                continue
            if POSITIVE_DECIMAL_PATTERN.fullmatch(name) is None:
                self._problem(
                    relative,
                    "unexpected_path",
                    "Ticket directory name is not a canonical positive decimal.",
                    {"entry": name},
                )
                continue
            if not self._require_directory(path, relative):
                continue
            ticket_id = int(name)
            self.known_ticket_ids.append(ticket_id)
            self._scan_ticket_directory(path, ticket_id)

    def _scan_ticket_directory(self, directory: Path, ticket_id: int) -> None:
        relative = f"tickets/{ticket_id}"
        entries = self._entries(directory, relative)
        if entries is None:
            return
        expected = {"ticket.md", "tasks"}
        for name in sorted(entries):
            if name in expected:
                continue
            path = f"{relative}/{name}"
            if is_private_temp(name):
                self._problem(
                    path,
                    "stale_temporary",
                    "A private Wyrd temporary entry remains.",
                    {"entry": name},
                )
            else:
                self._problem(
                    path,
                    "unexpected_path",
                    "An unexpected path exists in a ticket directory.",
                    {"entry": name},
                )

        ticket_path = directory / "ticket.md"
        ticket_relative = f"{relative}/ticket.md"
        if self._require_file(ticket_path, ticket_relative):
            try:
                ticket = decode_ticket(
                    read_bytes_nofollow(ticket_path), expected_id=ticket_id
                )
                self.tickets.append(LocatedTicket(path=ticket_relative, ticket=ticket))
            except CodecFailure as error:
                self._codec_problem(ticket_relative, error)
            except ApplicationError as error:
                self._application_problem(ticket_relative, error)

        tasks_dir = directory / "tasks"
        tasks_relative = f"{relative}/tasks"
        if self._require_directory(tasks_dir, tasks_relative):
            self._scan_tasks(tasks_dir, ticket_id)

    def _scan_tasks(self, directory: Path, ticket_id: int) -> None:
        relative = f"tickets/{ticket_id}/tasks"
        entries = self._entries(directory, relative)
        if entries is None:
            return
        for name in sorted(entries, key=_entry_sort_key):
            path = entries[name]
            task_relative = f"{relative}/{name}"
            if is_private_temp(name):
                self._problem(
                    task_relative,
                    "stale_temporary",
                    "A private Wyrd temporary entry remains.",
                    {"entry": name},
                )
                continue
            match = TASK_FILE_PATTERN.fullmatch(name)
            if match is None:
                self._problem(
                    task_relative,
                    "malformed_task_context",
                    "Task filename is not a canonical positive decimal followed by .md.",
                    {"entry": name},
                )
                continue
            if not self._require_file(path, task_relative):
                continue
            number = int(match.group(1))
            self.known_task_identities.append(
                TaskIdentity(ticket_id=ticket_id, number=number)
            )
            try:
                task = decode_task(
                    read_bytes_nofollow(path),
                    ticket_id=ticket_id,
                    expected_number=number,
                )
                self.tasks.append(LocatedTask(path=task_relative, task=task))
            except CodecFailure as error:
                self._codec_problem(task_relative, error)
            except ApplicationError as error:
                self._application_problem(task_relative, error)

    def _entries(self, directory: Path, relative: str) -> dict[str, Path] | None:
        try:
            with os.scandir(directory) as iterator:
                return {entry.name: Path(entry.path) for entry in iterator}
        except OSError as error:
            self._problem(
                relative,
                "unreadable_path",
                "A managed directory could not be scanned.",
                {"reason": str(error)},
            )
            return None

    def _require_file(self, path: Path, relative: str) -> bool:
        return self._require_kind(path, relative, "file")

    def _require_directory(self, path: Path, relative: str) -> bool:
        return self._require_kind(path, relative, "directory")

    def _require_kind(self, path: Path, relative: str, expected: str) -> bool:
        actual = lstat_kind(path)
        if actual == expected:
            return True
        code = "missing_path" if actual == "missing" else "symlink" if actual == "symlink" else "wrong_path_type"
        self._problem(
            relative,
            code,
            f"Managed path must be a {expected}, not {actual}.",
            {"expected_type": expected, "actual_type": actual},
        )
        return False

    def _codec_problem(self, path: str, error: CodecFailure) -> None:
        self._problem(path, error.code, error.message, error.details)

    def _application_problem(self, path: str, error: ApplicationError) -> None:
        self._problem(path, error.code, error.message, error.details)

    def _problem(
        self, path: str, code: str, message: str, details: dict[str, object]
    ) -> None:
        self.problems.append(
            DoctorProblemDTO(path=path, code=code, message=message, details=details)
        )


def _entry_sort_key(name: str) -> tuple[int, int | str]:
    match = POSITIVE_DECIMAL_PATTERN.fullmatch(name)
    if match is not None:
        return (0, int(name))
    task_match = TASK_FILE_PATTERN.fullmatch(name)
    if task_match is not None:
        return (0, int(task_match.group(1)))
    return (1, name)
