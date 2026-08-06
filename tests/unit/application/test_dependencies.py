from __future__ import annotations

import pytest

from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.errors import (
    DependencyCycleError,
    InvalidDependencyScopeError,
    ResourceNotActiveError,
    TaskNotFoundError,
    TicketNotFoundError,
)
from wyrd_cli.domain.models import ResourceStatus, TaskIdentity
from tests.support.factories import task, ticket
from tests.support.fake_storage import FakeStorage, FixedClock


def test_ticket_dependency_direction_inverse_activity_and_revision_ownership() -> None:
    storage = FakeStorage(tickets=(ticket(1, revision=3), ticket(2, revision=7)))
    app = WyrdApplication(storage, FixedClock())

    blocked = app.add_dependency(1, 2)
    blocker = app.view_ticket(2)

    assert blocked.blocked_by == (2,)
    assert blocked.active_blocked_by == (2,)
    assert blocked.is_blocked
    assert blocked.revision == 4
    assert blocker.blocking == (1,)
    assert blocker.active_blocking == (1,)
    assert blocker.revision == 7
    assert storage.writes == [("update_ticket", 1)]


def test_task_dependency_uses_sibling_numbers_canonically_and_public_strings() -> None:
    first = task(12, 1, revision=6)
    third = task(12, 3, revision=2)
    storage = FakeStorage(tickets=(ticket(12, revision=9),), tasks=(first, third))
    result = WyrdApplication(storage, FixedClock()).add_dependency(
        third.identity, first.identity
    )
    assert result.id == "12.3"
    assert result.blocked_by == ("12.1",)
    assert result.revision == 3
    assert storage.tasks[12][3].blocked_by == (1,)
    assert storage.tasks[12][1].revision == 6
    assert storage.tickets[12].revision == 9
    assert storage.writes == [("update_task", "12.3")]


@pytest.mark.parametrize(
    "blocked, blocker",
    [
        (1, TaskIdentity(ticket_id=1, number=1)),
        (TaskIdentity(ticket_id=1, number=1), 1),
        (TaskIdentity(ticket_id=1, number=1), TaskIdentity(ticket_id=2, number=1)),
        (1, 1),
        (TaskIdentity(ticket_id=1, number=1), TaskIdentity(ticket_id=1, number=1)),
    ],
)
def test_invalid_dependency_scopes(blocked, blocker) -> None:
    storage = FakeStorage(
        tickets=(ticket(1), ticket(2)), tasks=(task(1, 1), task(2, 1))
    )
    with pytest.raises(InvalidDependencyScopeError):
        WyrdApplication(storage, FixedClock()).add_dependency(blocked, blocker)
    assert storage.writes == []


def test_identity_not_found_precedes_scope_validation() -> None:
    storage = FakeStorage(tickets=(ticket(1),), tasks=(task(1, 1),))
    app = WyrdApplication(storage, FixedClock())
    with pytest.raises(TicketNotFoundError):
        app.add_dependency(99, TaskIdentity(ticket_id=1, number=1))
    with pytest.raises(TaskNotFoundError):
        app.add_dependency(1, TaskIdentity(ticket_id=1, number=99))


def test_cycle_detection_uses_terminal_and_ineffective_relationships() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, blocked_by=(2,)),
            ticket(2, blocked_by=(3,), status=ResourceStatus.COMPLETED),
            ticket(3),
        )
    )
    with pytest.raises(DependencyCycleError):
        WyrdApplication(storage, FixedClock()).add_dependency(3, 1)
    assert storage.writes == []


def test_add_existing_relation_is_retry_safe_after_endpoints_close() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, blocked_by=(2,), status=ResourceStatus.DISMISSED),
            ticket(2, status=ResourceStatus.COMPLETED),
        )
    )
    clock = FixedClock()
    result = WyrdApplication(storage, clock).add_dependency(1, 2)
    assert result.blocked_by == (2,)
    assert storage.writes == []
    assert clock.calls == 0


def test_add_new_relation_requires_both_endpoints_active() -> None:
    storage = FakeStorage(
        tickets=(ticket(1), ticket(2, status=ResourceStatus.DISMISSED))
    )
    with pytest.raises(ResourceNotActiveError) as caught:
        WyrdApplication(storage, FixedClock()).add_dependency(1, 2)
    assert caught.value.details["resource_id"] == 2
    assert storage.writes == []


def test_remove_absent_relation_is_retry_safe_even_when_blocked_is_inactive() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, status=ResourceStatus.DISMISSED),
            ticket(2, status=ResourceStatus.COMPLETED),
        )
    )
    clock = FixedClock()
    result = WyrdApplication(storage, clock).remove_dependency(1, 2)
    assert result.blocked_by == ()
    assert storage.writes == []
    assert clock.calls == 0


def test_remove_existing_requires_only_blocked_resource_active() -> None:
    storage = FakeStorage(
        tickets=(ticket(1, blocked_by=(2,)), ticket(2, status=ResourceStatus.COMPLETED))
    )
    result = WyrdApplication(storage, FixedClock()).remove_dependency(1, 2)
    assert result.blocked_by == ()
    assert storage.writes == [("update_ticket", 1)]

    inactive = FakeStorage(
        tickets=(
            ticket(1, blocked_by=(2,), status=ResourceStatus.DISMISSED),
            ticket(2, status=ResourceStatus.COMPLETED),
        )
    )
    with pytest.raises(ResourceNotActiveError):
        WyrdApplication(inactive, FixedClock()).remove_dependency(1, 2)
    assert inactive.writes == []


def test_task_dependency_retry_safety_for_inactive_open_tasks() -> None:
    parent = ticket(1, status=ResourceStatus.DISMISSED)
    storage = FakeStorage(
        tickets=(parent,), tasks=(task(1, 1), task(1, 2, blocked_by=(1,)))
    )
    app = WyrdApplication(storage, FixedClock())
    assert app.add_dependency(
        TaskIdentity(ticket_id=1, number=2), TaskIdentity(ticket_id=1, number=1)
    ).blocked_by == ("1.1",)
    assert app.remove_dependency(
        TaskIdentity(ticket_id=1, number=1), TaskIdentity(ticket_id=1, number=2)
    ).blocked_by == ()
    assert storage.writes == []
