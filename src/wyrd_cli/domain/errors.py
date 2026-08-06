"""Stable, presentation-independent application and storage errors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar


class ApplicationError(Exception):
    """Base error exposed by the application boundary.

    ``code``, ``message``, and ``details`` are safe for presentation adapters.
    ``cause`` is retained only for debugging and must not be serialized.
    """

    code: ClassVar[str] = "application_error"

    def __init__(
        self,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})
        self.cause = cause


class IdentitySyntaxError(ApplicationError):
    """A resource identity does not match the public command syntax."""

    code = "usage_error"


class DomainValidationError(ApplicationError):
    code = "validation_error"


class InvalidLabelError(ApplicationError):
    code = "invalid_label"

    def __init__(self, label: str) -> None:
        super().__init__(f"Label '{label}' is invalid.", {"label": label})


class ProjectNotFoundError(ApplicationError):
    code = "project_not_found"

    def __init__(self) -> None:
        super().__init__(
            "No Wyrd project was found. Run 'wyrd init' in the intended directory."
        )


class ProjectAlreadyExistsError(ApplicationError):
    code = "project_already_exists"


class NestedProjectError(ApplicationError):
    code = "nested_project"


class InvalidProjectError(ApplicationError):
    code = "invalid_project"


class UnsupportedSchemaError(ApplicationError):
    code = "unsupported_schema"


class CorruptDataError(ApplicationError):
    code = "corrupt_data"


class TicketNotFoundError(ApplicationError):
    code = "ticket_not_found"

    def __init__(self, ticket_id: int) -> None:
        super().__init__(
            f"Ticket {ticket_id} was not found.", {"ticket_id": ticket_id}
        )


class TaskNotFoundError(ApplicationError):
    code = "task_not_found"

    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task {task_id} was not found.", {"task_id": task_id})


class ConflictError(ApplicationError):
    code = "conflict"


class ResourceNotActiveError(ApplicationError):
    code = "resource_not_active"

    def __init__(self, resource_id: int | str) -> None:
        super().__init__(
            f"Resource {resource_id} is not active.",
            {"resource_id": resource_id},
        )


class RevisionConflictError(ApplicationError):
    code = "revision_conflict"

    def __init__(
        self, resource_id: int | str, expected_revision: int, actual_revision: int
    ) -> None:
        super().__init__(
            f"Resource {resource_id} has revision {actual_revision}, not expected revision {expected_revision}.",
            {
                "resource_id": resource_id,
                "expected_revision": expected_revision,
                "actual_revision": actual_revision,
            },
        )


class InvalidDependencyScopeError(ApplicationError):
    code = "invalid_dependency_scope"

    def __init__(self, blocked_id: int | str, blocker_id: int | str) -> None:
        super().__init__(
            "Dependencies are allowed only between tickets or between sibling tasks.",
            {"blocked_id": blocked_id, "blocker_id": blocker_id},
        )


class DependencyCycleError(ApplicationError):
    code = "dependency_cycle"

    def __init__(self, blocked_id: int | str, blocker_id: int | str) -> None:
        super().__init__(
            f"Adding dependency {blocked_id} -> {blocker_id} would create a cycle.",
            {"blocked_id": blocked_id, "blocker_id": blocker_id},
        )


class BlockedByOpenDependencyError(ApplicationError):
    code = "blocked_by_open_dependency"

    def __init__(self, resource_id: int | str) -> None:
        super().__init__(
            f"Resource {resource_id} is blocked by an active dependency.",
            {"resource_id": resource_id},
        )


class TicketHasOpenTasksError(ApplicationError):
    code = "ticket_has_open_tasks"

    def __init__(self, ticket_id: int) -> None:
        super().__init__(
            f"Ticket {ticket_id} has open tasks.", {"ticket_id": ticket_id}
        )


class StorageError(ApplicationError):
    """Base for persistence failures that are not domain failures."""

    code = "storage_error"


class StorageConflictError(StorageError):
    """A create-if-absent or revision-aware persistence operation conflicted."""

    code = "conflict"


class StorageTransactionError(StorageError):
    """A transaction could not be started, committed, or safely completed."""

    code = "transaction_error"


class LockTimeoutError(StorageTransactionError):
    code = "lock_timeout"
