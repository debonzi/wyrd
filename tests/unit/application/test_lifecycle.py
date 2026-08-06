from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.errors import (
    BlockedByOpenDependencyError,
    ConflictError,
    ResourceNotActiveError,
    RevisionConflictError,
    TicketHasOpenTasksError,
)
from wyrd_cli.domain.models import ResourceStatus, TaskIdentity
from tests.support.factories import task, ticket
from tests.support.fake_storage import FakeStorage, FixedClock

CLOSED = datetime(2026, 8, 5, 20, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "tasks",
    [
        (),
        (
            task(1, 1, status=ResourceStatus.COMPLETED),
            task(1, 2, status=ResourceStatus.DISMISSED),
        ),
    ],
)
def test_complete_ticket_with_no_tasks_or_all_terminal(tasks: tuple) -> None:
    storage = FakeStorage(tickets=(ticket(1, revision=2),), tasks=tasks)
    result = WyrdApplication(storage, FixedClock(CLOSED)).complete_ticket(1)
    assert result.status is ResourceStatus.COMPLETED
    assert result.revision == 3
    assert result.updated_at == result.closed_at == CLOSED
    assert storage.writes == [("update_ticket", 1)]


def test_ticket_completion_checks_open_tasks_before_blockers_and_never_writes() -> None:
    storage = FakeStorage(
        tickets=(ticket(1, blocked_by=(2,)), ticket(2)),
        tasks=(task(1, 1),),
    )
    with pytest.raises(TicketHasOpenTasksError):
        WyrdApplication(storage, FixedClock()).complete_ticket(1)
    assert storage.writes == []


def test_ticket_completion_rejects_effective_blocker() -> None:
    storage = FakeStorage(tickets=(ticket(1, blocked_by=(2,)), ticket(2)))
    with pytest.raises(BlockedByOpenDependencyError):
        WyrdApplication(storage, FixedClock()).complete_ticket(1)
    assert storage.writes == []


def test_closed_blocker_does_not_prevent_completion() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, blocked_by=(2,)),
            ticket(2, status=ResourceStatus.COMPLETED),
        )
    )
    result = WyrdApplication(storage, FixedClock(CLOSED)).complete_ticket(1)
    assert result.status is ResourceStatus.COMPLETED
    assert result.blocked_by == (2,)
    assert result.active_blocked_by == ()


def test_ticket_dismissal_ignores_blockers_and_open_tasks_without_cascade() -> None:
    child = task(1, 1)
    storage = FakeStorage(
        tickets=(ticket(1, blocked_by=(2,)), ticket(2)), tasks=(child,)
    )
    result = WyrdApplication(storage, FixedClock(CLOSED)).dismiss_ticket(1)
    assert result.status is ResourceStatus.DISMISSED
    assert result.tasks_summary.inactive_open == 1
    assert storage.tasks[1][1] == child
    assert storage.writes == [("update_ticket", 1)]


def test_task_complete_honors_effective_blocker_and_dismiss_ignores_it() -> None:
    blocked = task(1, 2, blocked_by=(1,))
    storage = FakeStorage(tickets=(ticket(1),), tasks=(task(1, 1), blocked))
    app = WyrdApplication(storage, FixedClock(CLOSED))
    with pytest.raises(BlockedByOpenDependencyError):
        app.complete_task(blocked.identity)
    result = app.dismiss_task(blocked.identity)
    assert result.status is ResourceStatus.DISMISSED
    assert result.blocked_by == ("1.1",)
    assert storage.writes == [("update_task", "1.2")]


def test_inactive_open_task_cannot_transition() -> None:
    storage = FakeStorage(
        tickets=(ticket(1, status=ResourceStatus.DISMISSED),),
        tasks=(task(1, 1),),
    )
    app = WyrdApplication(storage, FixedClock(CLOSED))
    with pytest.raises(ResourceNotActiveError):
        app.complete_task(TaskIdentity(ticket_id=1, number=1))
    with pytest.raises(ResourceNotActiveError):
        app.dismiss_task(TaskIdentity(ticket_id=1, number=1))
    assert storage.writes == []


@pytest.mark.parametrize("kind", ["ticket", "task"])
def test_transition_idempotency_uses_no_clock_or_write(kind: str) -> None:
    closed_ticket = ticket(1, status=ResourceStatus.COMPLETED, revision=4)
    tasks = (
        (task(1, 1, status=ResourceStatus.COMPLETED, revision=3),)
        if kind == "task"
        else ()
    )
    storage = FakeStorage(tickets=(closed_ticket,), tasks=tasks)
    clock = FixedClock(CLOSED)
    app = WyrdApplication(storage, clock)
    result = (
        app.complete_task(TaskIdentity(ticket_id=1, number=1))
        if kind == "task"
        else app.complete_ticket(1)
    )
    assert result.status is ResourceStatus.COMPLETED
    assert storage.writes == []
    assert clock.calls == 0


def test_opposite_terminal_transition_conflicts_without_write() -> None:
    storage = FakeStorage(
        tickets=(ticket(1, status=ResourceStatus.COMPLETED),)
    )
    with pytest.raises(ConflictError):
        WyrdApplication(storage, FixedClock()).dismiss_ticket(1)
    assert storage.writes == []


def test_preflight_and_mutation_are_separate_transactions_and_revision_is_rechecked() -> None:
    storage = FakeStorage(tickets=(ticket(1, revision=2),))
    app = WyrdApplication(storage, FixedClock(CLOSED))
    preflight = app.transition_ticket_preflight(1, ResourceStatus.COMPLETED)
    assert preflight.revision == 2
    assert storage.read_transactions == 1
    storage.tickets[1] = ticket(1, revision=3)

    with pytest.raises(RevisionConflictError):
        app.complete_ticket(1, expected_revision=preflight.revision)

    assert storage.write_transactions == 1
    assert storage.writes == []
