"""Linux filesystem implementation of Wyrd's application storage port."""

from __future__ import annotations

import errno
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from wyrd_cli.application.storage import ReadTransaction, StructuralScan, WriteTransaction
from wyrd_cli.domain.errors import (
    ApplicationError,
    ConflictError,
    CorruptDataError,
    InvalidProjectError,
    NestedProjectError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    StorageConflictError,
    StorageTransactionError,
    UnsupportedSchemaError,
)
from wyrd_cli.domain.models import Project, Task, TaskIdentity, Ticket

from .atomic import AtomicOperations, FaultInjector
from .codec import (
    CodecFailure,
    decode_project,
    decode_task,
    decode_ticket,
    encode_project,
    encode_task,
    encode_ticket,
)
from .diagnostics import StructuralScanner
from .locking import project_lock
from .paths import (
    POSITIVE_DECIMAL_PATTERN,
    TASK_FILE_PATTERN,
    is_init_staging,
    is_private_temp,
    lstat_kind,
    read_bytes_nofollow,
    require_project_directory,
)


@dataclass
class _StrictSnapshot:
    project: Project
    tickets: dict[int, Ticket]
    tasks: dict[int, dict[int, Task]]


class FilesystemStorage:
    """Production Linux-local filesystem adapter.

    ``discover`` or ``initialize`` binds the instance to one validated project.
    Transactions use the persistent empty ``.wyrd/lock`` file and keep one
    shared or exclusive ``flock`` for the complete context lifetime. If atomic
    publication succeeds but the following directory ``fsync`` fails, the
    operation raises while the newly published resource may remain visible;
    its canonical bytes are nevertheless complete and safe to revalidate.
    """

    def __init__(
        self,
        *,
        fault_injector: FaultInjector | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.01,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._atomic = AtomicOperations(fault_injector)
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._root: Path | None = None

    @property
    def bound_root(self) -> str | None:
        """Absolute bound root for composition/debugging, never required by application."""

        return None if self._root is None else str(self._root)

    def suggested_project_name(self, root: str) -> str:
        return _absolute(root).name

    def discover(self, start: str) -> Project:
        root = _discover_unique_root(start)
        project = _load_discovery_project(root)
        self._root = root
        return project

    def discover_for_diagnostics(self, start: str) -> None:
        root = _discover_unique_root(start)
        require_project_directory(root, label="project root")
        require_project_directory(root / ".wyrd", label=".wyrd directory")
        lock = root / ".wyrd" / "lock"
        if lstat_kind(lock) != "file" or lock.lstat().st_size != 0:
            raise InvalidProjectError(
                "The persistent project lock is missing, malformed, or unsafe.",
                {"path": "lock"},
            )
        self._root = root

    def initialize(
        self,
        *,
        root: str,
        name: str,
        created_at: datetime,
        name_was_explicit: bool = True,
    ) -> Project:
        root_path = _absolute(root)
        require_project_directory(root_path, label="project root")
        candidates = _project_candidates(root_path)
        current_candidate = root_path / ".wyrd"
        current_exists = lstat_kind(current_candidate) != "missing"
        ancestor_candidates = [
            candidate for candidate in candidates if candidate != current_candidate
        ]

        if current_exists:
            if ancestor_candidates:
                raise NestedProjectError(
                    "Nested Wyrd projects are not supported.",
                    {
                        "roots": [
                            str(candidate.parent)
                            for candidate in (current_candidate, *ancestor_candidates)
                        ]
                    },
                )
            return self._existing_initialization(
                root_path, name, name_was_explicit=name_was_explicit
            )
        if ancestor_candidates:
            if len(ancestor_candidates) > 1:
                raise NestedProjectError(
                    "Nested Wyrd projects are not supported.",
                    {"roots": [str(candidate.parent) for candidate in ancestor_candidates]},
                )
            raise ProjectAlreadyExistsError(
                "A Wyrd project already exists in an ancestor directory.",
                {"root": str(ancestor_candidates[0].parent)},
            )

        project = Project(root=str(root_path), name=name, created_at=created_at)
        staging = self._atomic.unique_init_staging(root_path)
        try:
            self._atomic.hit("open", staging)
            os.mkdir(staging, 0o700)
            self._atomic.write_new_file(staging / "project.yaml", encode_project(project))
            self._atomic.write_new_file(staging / "lock", b"")
            self._atomic.hit("open", staging / "tickets")
            os.mkdir(staging / "tickets", 0o700)
            self._atomic.fsync_directory(staging / "tickets")
            self._atomic.fsync_directory(staging)
            if lstat_kind(root_path / ".wyrd") != "missing":
                raise FileExistsError(errno.EEXIST, "project appeared concurrently")
            self._atomic.publish_directory_absent(staging, root_path / ".wyrd")
        except OSError as error:
            if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                self._cleanup_init_staging(staging)
                return self._existing_initialization(
                    root_path, name, name_was_explicit=name_was_explicit
                )
            self._cleanup_init_staging(staging, suppress=True)
            raise StorageTransactionError(
                "Project initialization could not be completed safely.",
                {"root": str(root_path)},
                cause=error,
            ) from error
        except ApplicationError:
            self._cleanup_init_staging(staging, suppress=True)
            raise
        except Exception as error:
            self._cleanup_init_staging(staging, suppress=True)
            raise StorageTransactionError(
                "Project initialization could not be completed safely.",
                {"root": str(root_path)},
                cause=error,
            ) from error

        self._root = root_path
        return project

    @contextmanager
    def read(self, *, timeout: float = 10.0) -> Iterator[ReadTransaction]:
        with self._transaction(writable=False, timeout=timeout) as transaction:
            yield transaction

    @contextmanager
    def write(self, *, timeout: float = 10.0) -> Iterator[WriteTransaction]:
        with self._transaction(writable=True, timeout=timeout) as transaction:
            yield transaction

    @contextmanager
    def _transaction(
        self, *, writable: bool, timeout: float
    ) -> Iterator["_FilesystemTransaction"]:
        root = self._require_bound_root()
        require_project_directory(root, label="project root")
        require_project_directory(root / ".wyrd", label=".wyrd directory")
        with project_lock(
            root / ".wyrd" / "lock",
            exclusive=writable,
            timeout=timeout,
            monotonic=self._monotonic,
            sleep=self._sleep,
            poll_interval=self._poll_interval,
        ):
            require_project_directory(root, label="project root")
            require_project_directory(root / ".wyrd", label=".wyrd directory")
            transaction = _FilesystemTransaction(root, self._atomic, writable=writable)
            try:
                yield transaction
            except BaseException:
                raise
            else:
                transaction.commit()
            finally:
                transaction.close()

    def _existing_initialization(
        self, root: Path, requested_name: str, *, name_was_explicit: bool
    ) -> Project:
        snapshot = _load_strict_snapshot(root)
        if name_was_explicit and snapshot.project.name != requested_name:
            raise ConflictError(
                "A project with a different name already exists.",
                {
                    "existing_name": snapshot.project.name,
                    "requested_name": requested_name,
                },
            )
        self._root = root
        return snapshot.project

    def _cleanup_init_staging(self, staging: Path, *, suppress: bool = False) -> None:
        if not is_init_staging(staging.name) or lstat_kind(staging) == "missing":
            return
        try:
            self._atomic.cleanup_owned_tree(staging)
        except Exception:
            if not suppress:
                raise

    def _require_bound_root(self) -> Path:
        if self._root is None:
            raise InvalidProjectError(
                "Filesystem storage is not bound to a discovered project."
            )
        return self._root


class _FilesystemTransaction:
    def __init__(
        self, root: Path, atomic: AtomicOperations, *, writable: bool
    ) -> None:
        self.root = root
        self.wyrd = root / ".wyrd"
        self.atomic = atomic
        self.writable = writable
        self.active = True
        self._cached_snapshot: _StrictSnapshot | None = None
        self._pending_commit: Callable[[], None] | None = None

    def close(self) -> None:
        self.active = False

    def commit(self) -> None:
        self._ensure_active()
        if self._pending_commit is not None:
            self._pending_commit()
            self._pending_commit = None

    def load_project(self) -> Project:
        return self._snapshot().project

    def list_tickets(self) -> tuple[Ticket, ...]:
        return tuple(self._snapshot().tickets.values())

    def get_ticket(self, ticket_id: int) -> Ticket | None:
        return self._snapshot().tickets.get(ticket_id)

    def list_tasks(self, ticket_id: int) -> tuple[Task, ...]:
        return tuple(self._snapshot().tasks.get(ticket_id, {}).values())

    def get_task(self, identity: TaskIdentity) -> Task | None:
        return self._snapshot().tasks.get(identity.ticket_id, {}).get(identity.number)

    def structural_scan(self) -> StructuralScan:
        self._ensure_active()
        return StructuralScanner(self.root).scan()

    def create_ticket(self, ticket: Ticket) -> None:
        self._ensure_writable()
        snapshot = self._snapshot()
        if ticket.id in snapshot.tickets:
            raise StorageConflictError(
                f"Ticket {ticket.id} already exists.", {"ticket_id": ticket.id}
            )
        tickets_dir = self.wyrd / "tickets"
        destination = tickets_dir / str(ticket.id)
        if lstat_kind(destination) != "missing":
            raise StorageConflictError(
                f"Ticket {ticket.id} already exists.", {"ticket_id": ticket.id}
            )
        encoded = encode_ticket(ticket)
        self._stage_commit(
            lambda: self._publish_ticket(ticket, tickets_dir, destination, encoded)
        )
        snapshot.tickets[ticket.id] = ticket
        snapshot.tasks[ticket.id] = {}

    def update_ticket(self, ticket: Ticket, *, expected_revision: int) -> None:
        self._ensure_writable()
        snapshot = self._snapshot()
        current = snapshot.tickets.get(ticket.id)
        if current is None:
            raise StorageConflictError(
                f"Ticket {ticket.id} no longer exists.", {"ticket_id": ticket.id}
            )
        self._check_revision(
            current.revision, expected_revision, resource_id=ticket.id
        )
        destination = self.wyrd / "tickets" / str(ticket.id) / "ticket.md"
        persisted = _decode_ticket_path(destination, ticket.id, self.wyrd)
        self._check_revision(
            persisted.revision, expected_revision, resource_id=ticket.id
        )
        encoded = encode_ticket(ticket)
        self._stage_commit(
            lambda: self._replace_resource(destination, encoded, "update ticket")
        )
        snapshot.tickets[ticket.id] = ticket

    def create_task(self, task: Task) -> None:
        self._ensure_writable()
        snapshot = self._snapshot()
        if task.ticket_id not in snapshot.tickets:
            raise StorageConflictError(
                f"Parent ticket {task.ticket_id} no longer exists.",
                {"ticket_id": task.ticket_id},
            )
        siblings = snapshot.tasks[task.ticket_id]
        if task.number in siblings:
            raise StorageConflictError(
                f"Task {task.public_id} already exists.", {"task_id": task.public_id}
            )
        tasks_dir = self.wyrd / "tickets" / str(task.ticket_id) / "tasks"
        _require_managed_kind(tasks_dir, "directory", self.wyrd)
        destination = tasks_dir / f"{task.number}.md"
        if lstat_kind(destination) != "missing":
            raise StorageConflictError(
                f"Task {task.public_id} already exists.", {"task_id": task.public_id}
            )
        encoded = encode_task(task)
        self._stage_commit(
            lambda: self._publish_task(task, destination, encoded)
        )
        siblings[task.number] = task

    def update_task(self, task: Task, *, expected_revision: int) -> None:
        self._ensure_writable()
        snapshot = self._snapshot()
        current = snapshot.tasks.get(task.ticket_id, {}).get(task.number)
        if current is None:
            raise StorageConflictError(
                f"Task {task.public_id} no longer exists.", {"task_id": task.public_id}
            )
        self._check_revision(
            current.revision, expected_revision, resource_id=task.public_id
        )
        destination = (
            self.wyrd
            / "tickets"
            / str(task.ticket_id)
            / "tasks"
            / f"{task.number}.md"
        )
        persisted = _decode_task_path(
            destination, task.ticket_id, task.number, self.wyrd
        )
        self._check_revision(
            persisted.revision, expected_revision, resource_id=task.public_id
        )
        encoded = encode_task(task)
        self._stage_commit(
            lambda: self._replace_resource(destination, encoded, "update task")
        )
        snapshot.tasks[task.ticket_id][task.number] = task

    def _stage_commit(self, operation: Callable[[], None]) -> None:
        if self._pending_commit is not None:
            raise StorageTransactionError(
                "A Wyrd mutation may publish at most one canonical resource."
            )
        self._pending_commit = operation

    def _publish_ticket(
        self,
        ticket: Ticket,
        tickets_dir: Path,
        destination: Path,
        encoded: bytes,
    ) -> None:
        staging = self.atomic.unique_temp(tickets_dir)
        try:
            self.atomic.hit("open", staging)
            os.mkdir(staging, 0o700)
            self.atomic.hit("open", staging / "tasks")
            os.mkdir(staging / "tasks", 0o700)
            self.atomic.write_new_file(staging / "ticket.md", encoded)
            self.atomic.fsync_directory(staging / "tasks")
            self.atomic.fsync_directory(staging)
            self.atomic.publish_directory_absent(staging, destination)
        except OSError as error:
            self._cleanup_ticket_staging(staging)
            if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                raise StorageConflictError(
                    f"Ticket {ticket.id} already exists.",
                    {"ticket_id": ticket.id},
                    cause=error,
                ) from error
            raise _storage_failure("create ticket", error) from error
        except ApplicationError:
            self._cleanup_ticket_staging(staging)
            raise
        except Exception as error:
            self._cleanup_ticket_staging(staging)
            raise _storage_failure("create ticket", error) from error

    def _publish_task(self, task: Task, destination: Path, encoded: bytes) -> None:
        try:
            self.atomic.publish_file_absent(destination, encoded)
        except FileExistsError as error:
            raise StorageConflictError(
                f"Task {task.public_id} already exists.",
                {"task_id": task.public_id},
                cause=error,
            ) from error
        except ApplicationError:
            raise
        except Exception as error:
            raise _storage_failure("create task", error) from error

    def _replace_resource(
        self, destination: Path, encoded: bytes, operation: str
    ) -> None:
        try:
            self.atomic.replace_file(destination, encoded)
        except ApplicationError:
            raise
        except Exception as error:
            raise _storage_failure(operation, error) from error

    def _snapshot(self) -> _StrictSnapshot:
        self._ensure_active()
        if self._cached_snapshot is None:
            self._cached_snapshot = _load_strict_snapshot(self.root)
        return self._cached_snapshot

    def _ensure_active(self) -> None:
        if not self.active:
            raise StorageTransactionError(
                "A filesystem transaction cannot be used after context exit."
            )

    def _ensure_writable(self) -> None:
        self._ensure_active()
        if not self.writable:
            raise StorageTransactionError(
                "A read transaction cannot publish canonical resources."
            )

    def _cleanup_ticket_staging(self, staging: Path) -> None:
        if is_private_temp(staging.name) and lstat_kind(staging) != "missing":
            try:
                self.atomic.cleanup_owned_tree(staging)
            except Exception:
                pass

    @staticmethod
    def _check_revision(
        actual: int, expected: int, *, resource_id: int | str
    ) -> None:
        if actual != expected:
            raise StorageConflictError(
                f"Resource {resource_id} revision changed.",
                {
                    "resource_id": resource_id,
                    "expected_revision": expected,
                    "actual_revision": actual,
                },
            )


def create_filesystem_storage(
    *,
    fault_injector: FaultInjector | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval: float = 0.01,
) -> FilesystemStorage:
    """Public composition-root factory for the production filesystem adapter."""

    return FilesystemStorage(
        fault_injector=fault_injector,
        monotonic=monotonic,
        sleep=sleep,
        poll_interval=poll_interval,
    )


def _load_discovery_project(root: Path) -> Project:
    """Validate the discoverable base while leaving child records for transactions.

    This distinction is what lets a fresh-process doctor bind to a project whose
    ticket/task records are malformed and then report them tolerantly.
    """

    _validate_base_layout(root)
    project_path = root / ".wyrd" / "project.yaml"
    try:
        return decode_project(
            read_bytes_nofollow(project_path, invalid_project=True), root=str(root)
        )
    except UnsupportedSchemaError:
        raise
    except CodecFailure as error:
        raise _corrupt_codec("project.yaml", error) from error


def _load_strict_snapshot(root: Path) -> _StrictSnapshot:
    project = _load_discovery_project(root)
    wyrd = root / ".wyrd"
    tickets_dir = wyrd / "tickets"
    ticket_entries = _strict_entries(tickets_dir, "tickets", wyrd)
    tickets: dict[int, Ticket] = {}
    tasks: dict[int, dict[int, Task]] = {}
    for name in sorted(ticket_entries, key=_numeric_entry_sort):
        path = ticket_entries[name]
        if is_private_temp(name):
            continue
        if POSITIVE_DECIMAL_PATTERN.fullmatch(name) is None:
            raise CorruptDataError(
                f"Unexpected managed entry 'tickets/{name}'.",
                {"path": f"tickets/{name}"},
            )
        ticket_id = int(name)
        _require_managed_kind(path, "directory", wyrd)
        nested = _strict_entries(path, f"tickets/{name}", wyrd)
        _check_expected_entries(
            nested,
            expected={"ticket.md", "tasks"},
            parent=f"tickets/{name}",
            missing_invalid_project=False,
        )
        ticket_path = path / "ticket.md"
        task_dir = path / "tasks"
        _require_managed_kind(ticket_path, "file", wyrd)
        _require_managed_kind(task_dir, "directory", wyrd)
        ticket = _decode_ticket_path(ticket_path, ticket_id, wyrd)
        tickets[ticket_id] = ticket
        sibling_entries = _strict_entries(task_dir, f"tickets/{name}/tasks", wyrd)
        siblings: dict[int, Task] = {}
        for task_name in sorted(sibling_entries, key=_numeric_entry_sort):
            task_path = sibling_entries[task_name]
            if is_private_temp(task_name):
                continue
            match = TASK_FILE_PATTERN.fullmatch(task_name)
            if match is None:
                raise CorruptDataError(
                    f"Unexpected managed entry 'tickets/{name}/tasks/{task_name}'.",
                    {"path": f"tickets/{name}/tasks/{task_name}"},
                )
            number = int(match.group(1))
            _require_managed_kind(task_path, "file", wyrd)
            siblings[number] = _decode_task_path(
                task_path, ticket_id, number, wyrd
            )
        tasks[ticket_id] = siblings
    return _StrictSnapshot(project=project, tickets=tickets, tasks=tasks)


def _validate_base_layout(root: Path) -> None:
    require_project_directory(root, label="project root")
    wyrd = root / ".wyrd"
    require_project_directory(wyrd, label=".wyrd directory")
    entries = _strict_entries(wyrd, ".", wyrd)
    _check_expected_entries(
        entries,
        expected={"project.yaml", "lock", "tickets"},
        parent="",
        missing_invalid_project=True,
    )
    for name, kind in (
        ("project.yaml", "file"),
        ("lock", "file"),
        ("tickets", "directory"),
    ):
        path = wyrd / name
        actual = lstat_kind(path)
        if actual != kind:
            raise InvalidProjectError(
                f"Managed base path '{name}' must be a {kind}, not {actual}.",
                {"path": name, "expected_type": kind, "actual_type": actual},
            )
    lock_metadata = (wyrd / "lock").lstat()
    if lock_metadata.st_size != 0:
        raise InvalidProjectError(
            "The persistent project lock must be empty.",
            {"path": "lock", "size": lock_metadata.st_size},
        )


def _strict_entries(directory: Path, relative: str, wyrd: Path) -> dict[str, Path]:
    _require_managed_kind(directory, "directory", wyrd, base=(directory == wyrd))
    try:
        with os.scandir(directory) as iterator:
            return {entry.name: Path(entry.path) for entry in iterator}
    except OSError as error:
        raise CorruptDataError(
            f"Managed directory '{relative}' could not be scanned.",
            {"path": relative},
            cause=error,
        ) from error


def _check_expected_entries(
    entries: dict[str, Path],
    *,
    expected: set[str],
    parent: str,
    missing_invalid_project: bool,
) -> None:
    missing = sorted(expected - set(entries))
    if missing:
        path = f"{parent}/{missing[0]}" if parent else missing[0]
        error_type = InvalidProjectError if missing_invalid_project else CorruptDataError
        raise error_type(
            f"Required managed path '{path}' is missing.", {"path": path}
        )
    for name in sorted(set(entries) - expected):
        if not is_private_temp(name):
            path = f"{parent}/{name}" if parent else name
            raise CorruptDataError(
                f"Unexpected managed path '{path}'.", {"path": path}
            )


def _require_managed_kind(
    path: Path, expected: str, wyrd: Path, *, base: bool = False
) -> None:
    actual = lstat_kind(path)
    try:
        relative = path.relative_to(wyrd).as_posix() or "."
    except ValueError:
        relative = str(path)
    if actual == "symlink":
        raise InvalidProjectError(
            f"Managed path '{relative}' must not be a symbolic link.",
            {"path": relative},
        )
    if actual != expected:
        error_type = InvalidProjectError if base else CorruptDataError
        raise error_type(
            f"Managed path '{relative}' must be a {expected}, not {actual}.",
            {"path": relative, "expected_type": expected, "actual_type": actual},
        )


def _decode_ticket_path(path: Path, ticket_id: int, wyrd: Path) -> Ticket:
    relative = path.relative_to(wyrd).as_posix()
    _require_managed_kind(path, "file", wyrd)
    try:
        return decode_ticket(read_bytes_nofollow(path), expected_id=ticket_id)
    except CodecFailure as error:
        raise _corrupt_codec(relative, error) from error


def _decode_task_path(path: Path, ticket_id: int, number: int, wyrd: Path) -> Task:
    relative = path.relative_to(wyrd).as_posix()
    _require_managed_kind(path, "file", wyrd)
    try:
        return decode_task(
            read_bytes_nofollow(path),
            ticket_id=ticket_id,
            expected_number=number,
        )
    except CodecFailure as error:
        raise _corrupt_codec(relative, error) from error


def _corrupt_codec(path: str, error: CodecFailure) -> CorruptDataError:
    return CorruptDataError(
        f"Canonical data at '{path}' is invalid: {error.message}",
        {"path": path, "problem_code": error.code, **error.details},
        cause=error,
    )


def _storage_failure(operation: str, error: BaseException) -> StorageTransactionError:
    return StorageTransactionError(
        f"Filesystem transaction failed while attempting to {operation}.",
        {"operation": operation},
        cause=error,
    )


def _absolute(value: str) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _discover_unique_root(start: str) -> Path:
    candidates = _project_candidates(_absolute(start))
    if not candidates:
        raise ProjectNotFoundError()
    if len(candidates) > 1:
        raise NestedProjectError(
            "Nested Wyrd projects are not supported.",
            {"roots": [str(candidate.parent) for candidate in candidates]},
        )
    return candidates[0].parent


def _project_candidates(start: Path) -> list[Path]:
    candidates: list[Path] = []
    current = start
    while True:
        candidate = current / ".wyrd"
        if lstat_kind(candidate) != "missing":
            candidates.append(candidate)
        if current.parent == current:
            break
        current = current.parent
    return candidates


def _numeric_entry_sort(name: str) -> tuple[int, int | str]:
    if POSITIVE_DECIMAL_PATTERN.fullmatch(name):
        return (0, int(name))
    match = TASK_FILE_PATTERN.fullmatch(name)
    if match:
        return (0, int(match.group(1)))
    return (1, name)
