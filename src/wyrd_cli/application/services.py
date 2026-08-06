"""Transactionally orchestrated Wyrd application use cases."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import cast

from wyrd_cli.application.clock import Clock, read_clock
from wyrd_cli.application.dto import (
    UNSET,
    CreateResourceRequest,
    DoctorProblemDTO,
    DoctorReportDTO,
    EditResourceRequest,
    LabelUsageDTO,
    LabelsStatusDTO,
    ProjectDTO,
    ProjectStatusDTO,
    ResourceIdentity,
    TaskDTO,
    TaskListFilter,
    TaskStatusCountsDTO,
    TasksSummaryDTO,
    TicketDTO,
    TicketListFilter,
    TicketStatusCountsDTO,
    TransitionPreflightDTO,
)
from wyrd_cli.application.storage import ReadTransaction, StoragePort, StructuralScan
from wyrd_cli.domain.errors import (
    BlockedByOpenDependencyError,
    ConflictError,
    CorruptDataError,
    DependencyCycleError,
    DomainValidationError,
    InvalidDependencyScopeError,
    ResourceNotActiveError,
    RevisionConflictError,
    TaskNotFoundError,
    TicketHasOpenTasksError,
    TicketNotFoundError,
)
from wyrd_cli.domain.models import (
    Project,
    ResourceStatus,
    Task,
    TaskIdentity,
    Ticket,
    replace_task,
    replace_ticket,
)
from wyrd_cli.domain.rules import (
    cyclic_nodes,
    invert_edges,
    summarize_tasks,
    task_effective_blockers,
    task_is_active,
    ticket_effective_blockers,
    ticket_is_active,
    validate_task_graph,
    validate_ticket_graph,
    would_create_cycle,
)
from wyrd_cli.domain.values import (
    normalize_body,
    normalize_labels,
    normalize_search_text,
    searchable_text,
    validate_positive_int,
    validate_project_name,
    validate_title,
)


@dataclass
class _Snapshot:
    project: Project
    tickets: dict[int, Ticket]
    tasks: dict[int, dict[int, Task]]

    def all_tasks(self) -> tuple[Task, ...]:
        return tuple(
            task
            for ticket_id in sorted(self.tasks)
            for task in self.tasks[ticket_id].values()
        )


class WyrdApplication:
    """Stable, typed facade consumed by presentation adapters.

    Every ordinary read is completed within one shared transaction. Every
    mutation reloads and validates state in one exclusive transaction, then
    performs zero or one semantic storage write.
    """

    def __init__(self, storage: StoragePort, clock: Clock) -> None:
        self._storage = storage
        self._clock = clock

    # Project operations -------------------------------------------------

    def initialize(self, root: str, name: str | None = None) -> ProjectDTO:
        """Initialize the opaque target root atomically through storage."""
        selected_name = self._storage.suggested_project_name(root) if name is None else name
        selected_name = validate_project_name(selected_name)
        project = self._storage.initialize(
            root=root,
            name=selected_name,
            created_at=read_clock(self._clock),
            name_was_explicit=name is not None,
        )
        return _project_dto(project)

    def discover_project(self, start: str) -> ProjectDTO:
        """Discover and bind the unique project associated with an opaque start value."""
        return _project_dto(self._storage.discover(start))

    def bind_doctor_project(self, start: str) -> None:
        """Bind discovery without strict decoding so doctor can report malformed data."""
        self._storage.discover_for_diagnostics(start)

    def project_status(self, *, lock_timeout: float = 10.0) -> ProjectStatusDTO:
        """Return consistent project-wide counts from one read snapshot."""
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            ticket_statuses = Counter(ticket.status for ticket in snapshot.tickets.values())
            tasks = snapshot.all_tasks()
            task_statuses = Counter(task.status for task in tasks)
            active_open = sum(
                task_is_active(task, snapshot.tickets[task.ticket_id]) for task in tasks
            )
            ticket_blocked = sum(
                bool(ticket_effective_blockers(ticket, snapshot.tickets))
                for ticket in snapshot.tickets.values()
            )
            task_blocked = sum(
                bool(
                    task_effective_blockers(
                        task,
                        snapshot.tickets[task.ticket_id],
                        snapshot.tasks[task.ticket_id],
                    )
                )
                for task in tasks
            )
            labels = {
                label
                for resource in (*snapshot.tickets.values(), *tasks)
                for label in resource.labels
            }
            open_tasks = task_statuses[ResourceStatus.OPEN]
            result = ProjectStatusDTO(
                project=_project_dto(snapshot.project),
                tickets=TicketStatusCountsDTO(
                    total=len(snapshot.tickets),
                    open=ticket_statuses[ResourceStatus.OPEN],
                    completed=ticket_statuses[ResourceStatus.COMPLETED],
                    dismissed=ticket_statuses[ResourceStatus.DISMISSED],
                    blocked=ticket_blocked,
                ),
                tasks=TaskStatusCountsDTO(
                    total=len(tasks),
                    open=open_tasks,
                    completed=task_statuses[ResourceStatus.COMPLETED],
                    dismissed=task_statuses[ResourceStatus.DISMISSED],
                    active_open=active_open,
                    inactive_open=open_tasks - active_open,
                    blocked=task_blocked,
                ),
                labels=LabelsStatusDTO(distinct=len(labels)),
            )
            _validate_status_equations(result)
            return result

    # Ticket operations --------------------------------------------------

    def create_ticket(
        self, request: CreateResourceRequest, *, lock_timeout: float = 10.0
    ) -> TicketDTO:
        """Create one open ticket with a max-plus-one project identity."""
        title, body, labels = _normalize_create(request)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.write(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            ticket_id = max(snapshot.tickets, default=0) + 1
            now = read_clock(self._clock)
            ticket = Ticket(
                id=ticket_id,
                revision=1,
                title=title,
                body=body,
                status=ResourceStatus.OPEN,
                labels=labels,
                blocked_by=(),
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
            transaction.create_ticket(ticket)
            snapshot.tickets[ticket_id] = ticket
            snapshot.tasks[ticket_id] = {}
            return _ticket_dto(ticket, snapshot)

    def view_ticket(self, ticket_id: int, *, lock_timeout: float = 10.0) -> TicketDTO:
        """Return one complete ticket projection from a consistent snapshot."""
        ticket_id = validate_positive_int(ticket_id, "ticket_id")
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            return _ticket_dto(_require_ticket(snapshot, ticket_id), snapshot)

    def list_tickets(
        self,
        filters: TicketListFilter = TicketListFilter(),
        *,
        lock_timeout: float = 10.0,
    ) -> tuple[TicketDTO, ...]:
        """List complete ticket projections in ascending identity order."""
        status = _normalize_status_filter(filters.status)
        labels = normalize_labels(filters.labels)
        query = normalize_search_text(filters.text) if filters.text is not None else None
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            result: list[TicketDTO] = []
            for ticket in snapshot.tickets.values():
                if status != "all" and ticket.status is not status:
                    continue
                if not set(labels).issubset(ticket.labels):
                    continue
                if query is not None and query not in searchable_text(
                    f"{ticket.title}\n{ticket.body}"
                ):
                    continue
                result.append(_ticket_dto(ticket, snapshot))
            return tuple(result)

    def edit_ticket(
        self,
        ticket_id: int,
        request: EditResourceRequest,
        *,
        lock_timeout: float = 10.0,
    ) -> TicketDTO:
        """Apply an explicit active-ticket patch with optional revision checking."""
        ticket_id = validate_positive_int(ticket_id, "ticket_id")
        patch = _normalize_edit(request)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.write(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            ticket = _require_ticket(snapshot, ticket_id)
            _check_expected(ticket.id, ticket.revision, request.expected_revision)
            if not ticket_is_active(ticket):
                raise ResourceNotActiveError(ticket.id)
            title = ticket.title if patch.title is UNSET else cast(str, patch.title)
            body = ticket.body if patch.body is UNSET else cast(str, patch.body)
            labels = tuple(
                sorted(
                    (set(ticket.labels) - set(patch.remove_labels))
                    | set(patch.add_labels)
                )
            )
            if (title, body, labels) == (ticket.title, ticket.body, ticket.labels):
                return _ticket_dto(ticket, snapshot)
            now = read_clock(self._clock)
            updated = replace_ticket(
                ticket,
                title=title,
                body=body,
                labels=labels,
                revision=ticket.revision + 1,
                updated_at=now,
            )
            transaction.update_ticket(updated, expected_revision=ticket.revision)
            snapshot.tickets[ticket.id] = updated
            return _ticket_dto(updated, snapshot)

    def complete_ticket(
        self,
        ticket_id: int,
        *,
        expected_revision: int | None = None,
        lock_timeout: float = 10.0,
    ) -> TicketDTO:
        """Complete an eligible ticket, or return an idempotent existing result."""
        return self._transition_ticket(
            ticket_id,
            ResourceStatus.COMPLETED,
            expected_revision=expected_revision,
            lock_timeout=lock_timeout,
        )

    def dismiss_ticket(
        self,
        ticket_id: int,
        *,
        expected_revision: int | None = None,
        lock_timeout: float = 10.0,
    ) -> TicketDTO:
        """Dismiss a ticket without cascading to its tasks."""
        return self._transition_ticket(
            ticket_id,
            ResourceStatus.DISMISSED,
            expected_revision=expected_revision,
            lock_timeout=lock_timeout,
        )

    def transition_ticket_preflight(
        self,
        ticket_id: int,
        target_status: ResourceStatus,
        *,
        lock_timeout: float = 10.0,
    ) -> TransitionPreflightDTO:
        """Read confirmation data without retaining a transaction across a prompt."""
        ticket_id = validate_positive_int(ticket_id, "ticket_id")
        target_status = _validate_terminal_target(target_status)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            ticket = _require_ticket(snapshot, ticket_id)
            _check_terminal_request(ticket.id, ticket.status, target_status)
            open_tasks = sum(
                task.status is ResourceStatus.OPEN
                for task in snapshot.tasks.get(ticket.id, {}).values()
            )
            return TransitionPreflightDTO(
                id=ticket.id,
                title=ticket.title,
                status=ticket.status,
                revision=ticket.revision,
                target_status=target_status,
                open_tasks=open_tasks,
            )

    def _transition_ticket(
        self,
        ticket_id: int,
        target_status: ResourceStatus,
        *,
        expected_revision: int | None,
        lock_timeout: float,
    ) -> TicketDTO:
        ticket_id = validate_positive_int(ticket_id, "ticket_id")
        target_status = _validate_terminal_target(target_status)
        if expected_revision is not None:
            validate_positive_int(expected_revision, "expected_revision")
        timeout = _validate_timeout(lock_timeout)
        with self._storage.write(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            ticket = _require_ticket(snapshot, ticket_id)
            _check_expected(ticket.id, ticket.revision, expected_revision)
            no_op = _check_terminal_request(ticket.id, ticket.status, target_status)
            if no_op:
                return _ticket_dto(ticket, snapshot)
            if target_status is ResourceStatus.COMPLETED:
                if any(
                    task.status is ResourceStatus.OPEN
                    for task in snapshot.tasks.get(ticket.id, {}).values()
                ):
                    raise TicketHasOpenTasksError(ticket.id)
                if ticket_effective_blockers(ticket, snapshot.tickets):
                    raise BlockedByOpenDependencyError(ticket.id)
            now = read_clock(self._clock)
            updated = replace_ticket(
                ticket,
                status=target_status,
                closed_at=now,
                updated_at=now,
                revision=ticket.revision + 1,
            )
            transaction.update_ticket(updated, expected_revision=ticket.revision)
            snapshot.tickets[ticket.id] = updated
            return _ticket_dto(updated, snapshot)

    # Task operations ----------------------------------------------------

    def create_task(
        self,
        ticket_id: int,
        request: CreateResourceRequest,
        *,
        lock_timeout: float = 10.0,
    ) -> TaskDTO:
        """Create one open task with a parent-local max-plus-one number."""
        ticket_id = validate_positive_int(ticket_id, "ticket_id")
        title, body, labels = _normalize_create(request)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.write(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            parent = _require_ticket(snapshot, ticket_id)
            if not ticket_is_active(parent):
                raise ResourceNotActiveError(parent.id)
            siblings = snapshot.tasks[parent.id]
            number = max(siblings, default=0) + 1
            now = read_clock(self._clock)
            task = Task(
                identity=TaskIdentity(ticket_id=parent.id, number=number),
                revision=1,
                title=title,
                body=body,
                status=ResourceStatus.OPEN,
                labels=labels,
                blocked_by=(),
                created_at=now,
                updated_at=now,
                closed_at=None,
            )
            transaction.create_task(task)
            siblings[number] = task
            return _task_dto(task, snapshot)

    def view_task(
        self, identity: TaskIdentity, *, lock_timeout: float = 10.0
    ) -> TaskDTO:
        """Return one complete task projection from a consistent snapshot."""
        identity = _validate_task_identity(identity)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            return _task_dto(_require_task(snapshot, identity), snapshot)

    def list_tasks(
        self,
        ticket_id: int,
        filters: TaskListFilter = TaskListFilter(),
        *,
        lock_timeout: float = 10.0,
    ) -> tuple[TaskDTO, ...]:
        """List tasks for exactly one existing ticket in task-number order."""
        ticket_id = validate_positive_int(ticket_id, "ticket_id")
        status = _normalize_status_filter(filters.status)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            _require_ticket(snapshot, ticket_id)
            return tuple(
                _task_dto(task, snapshot)
                for task in snapshot.tasks[ticket_id].values()
                if status == "all" or task.status is status
            )

    def edit_task(
        self,
        identity: TaskIdentity,
        request: EditResourceRequest,
        *,
        lock_timeout: float = 10.0,
    ) -> TaskDTO:
        """Apply an explicit active-task patch without mutating its parent."""
        identity = _validate_task_identity(identity)
        patch = _normalize_edit(request)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.write(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            task = _require_task(snapshot, identity)
            parent = snapshot.tickets[task.ticket_id]
            _check_expected(task.public_id, task.revision, request.expected_revision)
            if not task_is_active(task, parent):
                raise ResourceNotActiveError(task.public_id)
            title = task.title if patch.title is UNSET else cast(str, patch.title)
            body = task.body if patch.body is UNSET else cast(str, patch.body)
            labels = tuple(
                sorted(
                    (set(task.labels) - set(patch.remove_labels))
                    | set(patch.add_labels)
                )
            )
            if (title, body, labels) == (task.title, task.body, task.labels):
                return _task_dto(task, snapshot)
            now = read_clock(self._clock)
            updated = replace_task(
                task,
                title=title,
                body=body,
                labels=labels,
                revision=task.revision + 1,
                updated_at=now,
            )
            transaction.update_task(updated, expected_revision=task.revision)
            snapshot.tasks[task.ticket_id][task.number] = updated
            return _task_dto(updated, snapshot)

    def complete_task(
        self,
        identity: TaskIdentity,
        *,
        expected_revision: int | None = None,
        lock_timeout: float = 10.0,
    ) -> TaskDTO:
        """Complete an eligible active task, honoring effective blockers."""
        return self._transition_task(
            identity,
            ResourceStatus.COMPLETED,
            expected_revision=expected_revision,
            lock_timeout=lock_timeout,
        )

    def dismiss_task(
        self,
        identity: TaskIdentity,
        *,
        expected_revision: int | None = None,
        lock_timeout: float = 10.0,
    ) -> TaskDTO:
        """Dismiss an active task regardless of effective blockers."""
        return self._transition_task(
            identity,
            ResourceStatus.DISMISSED,
            expected_revision=expected_revision,
            lock_timeout=lock_timeout,
        )

    def transition_task_preflight(
        self,
        identity: TaskIdentity,
        target_status: ResourceStatus,
        *,
        lock_timeout: float = 10.0,
    ) -> TransitionPreflightDTO:
        """Read task confirmation data without retaining a transaction across a prompt."""
        identity = _validate_task_identity(identity)
        target_status = _validate_terminal_target(target_status)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            task = _require_task(snapshot, identity)
            no_op = _check_terminal_request(task.public_id, task.status, target_status)
            if not no_op and not task_is_active(task, snapshot.tickets[task.ticket_id]):
                raise ResourceNotActiveError(task.public_id)
            return TransitionPreflightDTO(
                id=task.public_id,
                title=task.title,
                status=task.status,
                revision=task.revision,
                target_status=target_status,
            )

    def _transition_task(
        self,
        identity: TaskIdentity,
        target_status: ResourceStatus,
        *,
        expected_revision: int | None,
        lock_timeout: float,
    ) -> TaskDTO:
        identity = _validate_task_identity(identity)
        target_status = _validate_terminal_target(target_status)
        if expected_revision is not None:
            validate_positive_int(expected_revision, "expected_revision")
        timeout = _validate_timeout(lock_timeout)
        with self._storage.write(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            task = _require_task(snapshot, identity)
            parent = snapshot.tickets[task.ticket_id]
            _check_expected(task.public_id, task.revision, expected_revision)
            no_op = _check_terminal_request(task.public_id, task.status, target_status)
            if no_op:
                return _task_dto(task, snapshot)
            if not task_is_active(task, parent):
                raise ResourceNotActiveError(task.public_id)
            if target_status is ResourceStatus.COMPLETED and task_effective_blockers(
                task, parent, snapshot.tasks[parent.id]
            ):
                raise BlockedByOpenDependencyError(task.public_id)
            now = read_clock(self._clock)
            updated = replace_task(
                task,
                status=target_status,
                closed_at=now,
                updated_at=now,
                revision=task.revision + 1,
            )
            transaction.update_task(updated, expected_revision=task.revision)
            snapshot.tasks[parent.id][task.number] = updated
            return _task_dto(updated, snapshot)

    # Dependency operations ---------------------------------------------

    def add_dependency(
        self,
        blocked: ResourceIdentity,
        blocker: ResourceIdentity,
        *,
        lock_timeout: float = 10.0,
    ) -> TicketDTO | TaskDTO:
        """Add one directed dependency after identity, scope, activity, and cycle checks."""
        return self._change_dependency(
            blocked, blocker, add=True, lock_timeout=lock_timeout
        )

    def remove_dependency(
        self,
        blocked: ResourceIdentity,
        blocker: ResourceIdentity,
        *,
        lock_timeout: float = 10.0,
    ) -> TicketDTO | TaskDTO:
        """Remove one directed dependency with retry-safe absent-relation behavior."""
        return self._change_dependency(
            blocked, blocker, add=False, lock_timeout=lock_timeout
        )

    def list_dependencies(
        self, identity: ResourceIdentity, *, lock_timeout: float = 10.0
    ) -> TicketDTO | TaskDTO:
        """Return a complete resource projection containing both dependency directions."""
        identity = _validate_resource_identity(identity)
        if isinstance(identity, TaskIdentity):
            return self.view_task(identity, lock_timeout=lock_timeout)
        return self.view_ticket(identity, lock_timeout=lock_timeout)

    def _change_dependency(
        self,
        blocked_identity: ResourceIdentity,
        blocker_identity: ResourceIdentity,
        *,
        add: bool,
        lock_timeout: float,
    ) -> TicketDTO | TaskDTO:
        blocked_identity = _validate_resource_identity(blocked_identity)
        blocker_identity = _validate_resource_identity(blocker_identity)
        timeout = _validate_timeout(lock_timeout)
        with self._storage.write(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            blocked_resource = _require_resource(snapshot, blocked_identity)
            blocker_resource = _require_resource(snapshot, blocker_identity)
            if type(blocked_identity) is not type(blocker_identity):
                raise InvalidDependencyScopeError(
                    _public_identity(blocked_identity), _public_identity(blocker_identity)
                )
            if isinstance(blocked_identity, TaskIdentity):
                blocker_task_identity = cast(TaskIdentity, blocker_identity)
                if (
                    blocked_identity.ticket_id != blocker_task_identity.ticket_id
                    or blocked_identity == blocker_task_identity
                ):
                    raise InvalidDependencyScopeError(
                        blocked_identity.public_id, blocker_task_identity.public_id
                    )
                blocked_task = cast(Task, blocked_resource)
                blocker_task = cast(Task, blocker_resource)
                relation_value = blocker_task.number
                relation_exists = relation_value in blocked_task.blocked_by
                if relation_exists == add:
                    return _task_dto(blocked_task, snapshot)
                parent = snapshot.tickets[blocked_task.ticket_id]
                if add:
                    if not task_is_active(blocked_task, parent):
                        raise ResourceNotActiveError(blocked_task.public_id)
                    if not task_is_active(blocker_task, parent):
                        raise ResourceNotActiveError(blocker_task.public_id)
                    edges = {
                        task.number: task.blocked_by
                        for task in snapshot.tasks[parent.id].values()
                    }
                    if would_create_cycle(edges, blocked_task.number, blocker_task.number):
                        raise DependencyCycleError(
                            blocked_task.public_id, blocker_task.public_id
                        )
                elif not task_is_active(blocked_task, parent):
                    raise ResourceNotActiveError(blocked_task.public_id)
                blockers = set(blocked_task.blocked_by)
                blockers.add(relation_value) if add else blockers.remove(relation_value)
                now = read_clock(self._clock)
                updated_task = replace_task(
                    blocked_task,
                    blocked_by=tuple(sorted(blockers)),
                    revision=blocked_task.revision + 1,
                    updated_at=now,
                )
                transaction.update_task(
                    updated_task, expected_revision=blocked_task.revision
                )
                snapshot.tasks[parent.id][blocked_task.number] = updated_task
                return _task_dto(updated_task, snapshot)

            blocked_ticket = cast(Ticket, blocked_resource)
            blocker_ticket = cast(Ticket, blocker_resource)
            if blocked_ticket.id == blocker_ticket.id:
                raise InvalidDependencyScopeError(blocked_ticket.id, blocker_ticket.id)
            relation_exists = blocker_ticket.id in blocked_ticket.blocked_by
            if relation_exists == add:
                return _ticket_dto(blocked_ticket, snapshot)
            if add:
                if not ticket_is_active(blocked_ticket):
                    raise ResourceNotActiveError(blocked_ticket.id)
                if not ticket_is_active(blocker_ticket):
                    raise ResourceNotActiveError(blocker_ticket.id)
                edges = {
                    ticket.id: ticket.blocked_by
                    for ticket in snapshot.tickets.values()
                }
                if would_create_cycle(edges, blocked_ticket.id, blocker_ticket.id):
                    raise DependencyCycleError(blocked_ticket.id, blocker_ticket.id)
            elif not ticket_is_active(blocked_ticket):
                raise ResourceNotActiveError(blocked_ticket.id)
            blockers = set(blocked_ticket.blocked_by)
            blockers.add(blocker_ticket.id) if add else blockers.remove(blocker_ticket.id)
            now = read_clock(self._clock)
            updated_ticket = replace_ticket(
                blocked_ticket,
                blocked_by=tuple(sorted(blockers)),
                revision=blocked_ticket.revision + 1,
                updated_at=now,
            )
            transaction.update_ticket(
                updated_ticket, expected_revision=blocked_ticket.revision
            )
            snapshot.tickets[blocked_ticket.id] = updated_ticket
            return _ticket_dto(updated_ticket, snapshot)

    # Derived read operations -------------------------------------------

    def list_labels(self, *, lock_timeout: float = 10.0) -> tuple[LabelUsageDTO, ...]:
        """Aggregate inline label usage across every canonical resource."""
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            snapshot = _load_snapshot(transaction)
            ticket_counts: Counter[str] = Counter()
            task_counts: Counter[str] = Counter()
            for ticket in snapshot.tickets.values():
                ticket_counts.update(ticket.labels)
            for task in snapshot.all_tasks():
                task_counts.update(task.labels)
            return tuple(
                LabelUsageDTO(
                    name=label,
                    ticket_count=ticket_counts[label],
                    task_count=task_counts[label],
                    total_count=ticket_counts[label] + task_counts[label],
                )
                for label in sorted(ticket_counts.keys() | task_counts.keys())
            )

    def doctor(self, *, lock_timeout: float = 10.0) -> DoctorReportDTO:
        """Combine tolerant structural and domain diagnostics without mutation."""
        timeout = _validate_timeout(lock_timeout)
        with self._storage.read(timeout=timeout) as transaction:
            scan = transaction.structural_scan()
            problems = [*scan.problems, *_domain_doctor_problems(scan)]
            problems.sort(key=_problem_sort_key)
            return DoctorReportDTO(healthy=not problems, problems=tuple(problems))


# Snapshot and projection helpers ----------------------------------------


def _load_snapshot(transaction: ReadTransaction) -> _Snapshot:
    project = transaction.load_project()
    tickets: dict[int, Ticket] = {}
    for ticket in sorted(transaction.list_tickets(), key=lambda item: item.id):
        if ticket.id in tickets:
            raise CorruptDataError(
                f"Duplicate ticket identity {ticket.id}.", {"ticket_id": ticket.id}
            )
        tickets[ticket.id] = ticket
    validate_ticket_graph(tickets)
    tasks: dict[int, dict[int, Task]] = {}
    for ticket_id, ticket in tickets.items():
        siblings: dict[int, Task] = {}
        for task in sorted(
            transaction.list_tasks(ticket_id), key=lambda item: item.number
        ):
            if task.number in siblings:
                raise CorruptDataError(
                    f"Duplicate task identity {task.public_id}.",
                    {"task_id": task.public_id},
                )
            siblings[task.number] = task
        validate_task_graph(ticket, siblings)
        if ticket.status is ResourceStatus.COMPLETED and any(
            task.status is ResourceStatus.OPEN for task in siblings.values()
        ):
            raise CorruptDataError(
                f"Completed ticket {ticket.id} has open tasks.",
                {"ticket_id": ticket.id},
            )
        tasks[ticket_id] = siblings
    return _Snapshot(project=project, tickets=tickets, tasks=tasks)


def _project_dto(project: Project) -> ProjectDTO:
    return ProjectDTO(**project.model_dump())


def _ticket_dto(ticket: Ticket, snapshot: _Snapshot) -> TicketDTO:
    edges = {item.id: item.blocked_by for item in snapshot.tickets.values()}
    blocking = invert_edges(edges).get(ticket.id, ())
    active_blocked_by = ticket_effective_blockers(ticket, snapshot.tickets)
    active_blocking = tuple(
        dependent_id
        for dependent_id in blocking
        if ticket_is_active(ticket)
        and ticket_is_active(snapshot.tickets[dependent_id])
    )
    tasks = snapshot.tasks.get(ticket.id, {})
    counts = summarize_tasks(tasks.values(), ticket)
    return TicketDTO(
        id=ticket.id,
        revision=ticket.revision,
        title=ticket.title,
        body=ticket.body,
        status=ticket.status,
        labels=ticket.labels,
        blocked_by=ticket.blocked_by,
        blocking=blocking,
        active_blocked_by=active_blocked_by,
        active_blocking=active_blocking,
        active=ticket_is_active(ticket),
        is_blocked=bool(active_blocked_by),
        tasks=tuple(task.public_id for task in tasks.values()),
        tasks_summary=TasksSummaryDTO(**counts.__dict__),
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
    )


def _task_dto(task: Task, snapshot: _Snapshot) -> TaskDTO:
    parent = snapshot.tickets[task.ticket_id]
    siblings = snapshot.tasks[task.ticket_id]
    edges = {item.number: item.blocked_by for item in siblings.values()}
    blocking_numbers = invert_edges(edges).get(task.number, ())
    active_blocker_numbers = task_effective_blockers(task, parent, siblings)
    active_blocking_numbers = tuple(
        number
        for number in blocking_numbers
        if task_is_active(task, parent) and task_is_active(siblings[number], parent)
    )
    prefix = f"{task.ticket_id}."
    return TaskDTO(
        id=task.public_id,
        ticket_id=task.ticket_id,
        number=task.number,
        revision=task.revision,
        title=task.title,
        body=task.body,
        status=task.status,
        labels=task.labels,
        blocked_by=tuple(prefix + str(number) for number in task.blocked_by),
        blocking=tuple(prefix + str(number) for number in blocking_numbers),
        active_blocked_by=tuple(prefix + str(number) for number in active_blocker_numbers),
        active_blocking=tuple(prefix + str(number) for number in active_blocking_numbers),
        active=task_is_active(task, parent),
        is_blocked=bool(active_blocker_numbers),
        created_at=task.created_at,
        updated_at=task.updated_at,
        closed_at=task.closed_at,
    )


# Validation helpers ------------------------------------------------------


def _normalize_create(request: CreateResourceRequest) -> tuple[str, str, tuple[str, ...]]:
    return (
        validate_title(request.title),
        normalize_body(request.body),
        normalize_labels(request.labels),
    )


def _normalize_edit(request: EditResourceRequest) -> EditResourceRequest:
    if (
        request.title is UNSET
        and request.body is UNSET
        and not request.add_labels
        and not request.remove_labels
    ):
        raise DomainValidationError("Edit requires at least one explicit change.")
    title = UNSET if request.title is UNSET else validate_title(cast(str, request.title))
    body = UNSET if request.body is UNSET else normalize_body(cast(str, request.body))
    added = normalize_labels(request.add_labels)
    removed = normalize_labels(request.remove_labels)
    overlap = sorted(set(added) & set(removed))
    if overlap:
        raise DomainValidationError(
            "The same label cannot be added and removed in one edit.",
            {"labels": overlap},
        )
    if request.expected_revision is not None:
        validate_positive_int(request.expected_revision, "expected_revision")
    return EditResourceRequest(
        title=title,
        body=body,
        add_labels=added,
        remove_labels=removed,
        expected_revision=request.expected_revision,
    )


def _normalize_status_filter(value: object) -> ResourceStatus | str:
    if value == "all":
        return "all"
    try:
        return value if isinstance(value, ResourceStatus) else ResourceStatus(value)
    except (TypeError, ValueError) as error:
        raise DomainValidationError(
            "Status must be open, completed, dismissed, or all."
        ) from error


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainValidationError("Lock timeout must be a non-negative number.")
    result = float(value)
    if result < 0 or not math.isfinite(result):
        raise DomainValidationError("Lock timeout must be a non-negative number.")
    return result


def _validate_terminal_target(value: ResourceStatus) -> ResourceStatus:
    try:
        status = value if isinstance(value, ResourceStatus) else ResourceStatus(value)
    except (TypeError, ValueError) as error:
        raise DomainValidationError("Target status must be completed or dismissed.") from error
    if not status.terminal:
        raise DomainValidationError("Target status must be completed or dismissed.")
    return status


def _validate_task_identity(identity: TaskIdentity) -> TaskIdentity:
    if not isinstance(identity, TaskIdentity):
        raise DomainValidationError("Task identity must be structured.")
    return identity


def _validate_resource_identity(identity: ResourceIdentity) -> ResourceIdentity:
    if isinstance(identity, TaskIdentity):
        return identity
    return validate_positive_int(identity, "ticket_id")


def _public_identity(identity: ResourceIdentity) -> int | str:
    return identity.public_id if isinstance(identity, TaskIdentity) else identity


def _check_expected(
    resource_id: int | str, actual_revision: int, expected_revision: int | None
) -> None:
    if expected_revision is not None and actual_revision != expected_revision:
        raise RevisionConflictError(resource_id, expected_revision, actual_revision)


def _check_terminal_request(
    resource_id: int | str,
    current: ResourceStatus,
    target: ResourceStatus,
) -> bool:
    if current is target:
        return True
    if current.terminal:
        raise ConflictError(
            f"Resource {resource_id} is already {current.value} and cannot become {target.value}.",
            {
                "resource_id": resource_id,
                "current_status": current.value,
                "requested_status": target.value,
            },
        )
    return False


def _require_ticket(snapshot: _Snapshot, ticket_id: int) -> Ticket:
    try:
        return snapshot.tickets[ticket_id]
    except KeyError as error:
        raise TicketNotFoundError(ticket_id) from error


def _require_task(snapshot: _Snapshot, identity: TaskIdentity) -> Task:
    try:
        return snapshot.tasks[identity.ticket_id][identity.number]
    except KeyError as error:
        raise TaskNotFoundError(identity.public_id) from error


def _require_resource(
    snapshot: _Snapshot, identity: ResourceIdentity
) -> Ticket | Task:
    if isinstance(identity, TaskIdentity):
        return _require_task(snapshot, identity)
    return _require_ticket(snapshot, identity)


def _validate_status_equations(status: ProjectStatusDTO) -> None:
    if status.tickets.total != (
        status.tickets.open + status.tickets.completed + status.tickets.dismissed
    ) or status.tasks.total != (
        status.tasks.open + status.tasks.completed + status.tasks.dismissed
    ) or status.tasks.open != status.tasks.active_open + status.tasks.inactive_open:
        raise CorruptDataError("Project status counts are internally inconsistent.")


# Domain doctor -----------------------------------------------------------


def _domain_doctor_problems(scan: StructuralScan) -> tuple[DoctorProblemDTO, ...]:
    problems: list[DoctorProblemDTO] = []
    ticket_records: dict[int, tuple[Ticket, str]] = {}
    task_records: dict[tuple[int, int], tuple[Task, str]] = {}
    known_ticket_ids = set(scan.known_ticket_ids)
    known_task_identities = {
        (identity.ticket_id, identity.number)
        for identity in scan.known_task_identities
    }

    for record in scan.tickets:
        if record.ticket.id in ticket_records:
            problems.append(
                DoctorProblemDTO(
                    path=record.path,
                    code="duplicate_identity",
                    message=f"Ticket ID {record.ticket.id} occurs more than once.",
                    details={"ticket_id": record.ticket.id},
                )
            )
        else:
            ticket_records[record.ticket.id] = (record.ticket, record.path)
        known_ticket_ids.add(record.ticket.id)
    for record in scan.tasks:
        key = (record.task.ticket_id, record.task.number)
        if key in task_records:
            problems.append(
                DoctorProblemDTO(
                    path=record.path,
                    code="duplicate_identity",
                    message=f"Task ID {record.task.public_id} occurs more than once.",
                    details={"task_id": record.task.public_id},
                )
            )
        else:
            task_records[key] = (record.task, record.path)
        known_task_identities.add(key)

    ticket_edges: dict[int, tuple[int, ...]] = {}
    for ticket_id, (ticket, path) in ticket_records.items():
        ticket_edges[ticket_id] = ticket.blocked_by
        for blocker_id in ticket.blocked_by:
            if blocker_id == ticket_id:
                problems.append(
                    DoctorProblemDTO(
                        path=path,
                        code="self_dependency",
                        message=f"Ticket {ticket_id} depends on itself.",
                        details={"ticket_id": ticket_id},
                    )
                )
            elif blocker_id not in known_ticket_ids:
                problems.append(
                    DoctorProblemDTO(
                        path=path,
                        code="dependency_target_not_found",
                        message=f"Ticket {ticket_id} references missing ticket {blocker_id}.",
                        details={"ticket_id": ticket_id, "blocker_id": blocker_id},
                    )
                )
    for ticket_id in cyclic_nodes(ticket_edges):
        ticket, path = ticket_records[ticket_id]
        problems.append(
            DoctorProblemDTO(
                path=path,
                code="dependency_cycle",
                message=f"Ticket {ticket.id} belongs to a dependency cycle.",
                details={"ticket_id": ticket.id},
            )
        )

    tasks_by_ticket: dict[int, dict[int, tuple[Task, str]]] = defaultdict(dict)
    for (ticket_id, number), record in task_records.items():
        task, path = record
        tasks_by_ticket[ticket_id][number] = record
        parent_record = ticket_records.get(ticket_id)
        if parent_record is None and ticket_id not in known_ticket_ids:
            problems.append(
                DoctorProblemDTO(
                    path=path,
                    code="invalid_hierarchy",
                    message=f"Task {task.public_id} has no existing parent ticket.",
                    details={"task_id": task.public_id, "ticket_id": ticket_id},
                )
            )
        elif (
            parent_record[0].status is ResourceStatus.COMPLETED
            and task.status is ResourceStatus.OPEN
        ):
            problems.append(
                DoctorProblemDTO(
                    path=path,
                    code="invalid_resource_activity",
                    message=f"Open task {task.public_id} belongs to a completed ticket.",
                    details={"task_id": task.public_id, "ticket_id": ticket_id},
                )
            )

    for ticket_id, records in tasks_by_ticket.items():
        edges = {number: task.blocked_by for number, (task, _) in records.items()}
        for number, (task, path) in records.items():
            for blocker_number in task.blocked_by:
                if blocker_number == number:
                    problems.append(
                        DoctorProblemDTO(
                            path=path,
                            code="self_dependency",
                            message=f"Task {task.public_id} depends on itself.",
                            details={"task_id": task.public_id},
                        )
                    )
                elif (ticket_id, blocker_number) not in known_task_identities:
                    problems.append(
                        DoctorProblemDTO(
                            path=path,
                            code="dependency_target_not_found",
                            message=f"Task {task.public_id} references missing sibling {ticket_id}.{blocker_number}.",
                            details={
                                "task_id": task.public_id,
                                "blocker_id": f"{ticket_id}.{blocker_number}",
                            },
                        )
                    )
        for number in cyclic_nodes(edges):
            task, path = records[number]
            problems.append(
                DoctorProblemDTO(
                    path=path,
                    code="dependency_cycle",
                    message=f"Task {task.public_id} belongs to a dependency cycle.",
                    details={"task_id": task.public_id},
                )
            )
    return tuple(problems)


def _problem_sort_key(problem: DoctorProblemDTO) -> tuple[str, str, str, str]:
    return (
        problem.path,
        problem.code,
        problem.message,
        repr(sorted(problem.details.items())),
    )
