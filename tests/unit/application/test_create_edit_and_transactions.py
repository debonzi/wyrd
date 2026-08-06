from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wyrd_cli.application.dto import CreateResourceRequest, EditResourceRequest, TicketListFilter
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.errors import (
    DomainValidationError,
    InvalidLabelError,
    ResourceNotActiveError,
    RevisionConflictError,
)
from wyrd_cli.domain.models import ResourceStatus, TaskIdentity
from tests.support.factories import NOW, task, ticket
from tests.support.fake_storage import FakeStorage, FixedClock

NEXT = datetime(2026, 8, 5, 19, 1, 2, tzinfo=UTC)


def test_create_ticket_defaults_clock_and_max_plus_one_with_gaps() -> None:
    storage = FakeStorage(tickets=(ticket(2), ticket(7)))
    clock = FixedClock(NEXT)
    app = WyrdApplication(storage, clock)

    result = app.create_ticket(
        CreateResourceRequest(
            title="  New ticket  ", body="a\r\nb", labels=("z", "bug", "bug")
        )
    )

    assert result.id == 8
    assert result.status is ResourceStatus.OPEN
    assert result.revision == 1
    assert result.title == "New ticket"
    assert result.body == "a\nb"
    assert result.labels == ("bug", "z")
    assert result.created_at == result.updated_at == NEXT
    assert result.closed_at is None
    assert result.tasks == ()
    assert clock.calls == 1
    assert storage.writes == [("create_ticket", 8)]
    assert storage.write_transactions == 1


def test_create_task_uses_local_max_plus_one_without_parent_mutation() -> None:
    parent = ticket(3, revision=9)
    storage = FakeStorage(
        tickets=(parent,), tasks=(task(3, 2), task(3, 8))
    )
    clock = FixedClock(NEXT)
    app = WyrdApplication(storage, clock)

    result = app.create_task(3, CreateResourceRequest(title="Task"))

    assert result.id == "3.9"
    assert result.ticket_id == 3
    assert storage.tickets[3] == parent
    assert storage.writes == [("create_task", "3.9")]
    assert clock.calls == 1


def test_invalid_create_input_fails_before_transaction_and_write() -> None:
    storage = FakeStorage()
    app = WyrdApplication(storage, FixedClock())
    with pytest.raises(InvalidLabelError):
        app.create_ticket(CreateResourceRequest(title="Title", labels=("BAD",)))
    assert storage.write_transactions == 0
    assert storage.writes == []


def test_edit_changes_all_fields_once_and_normalizes_labels() -> None:
    original = ticket(1, title="Old", body="old", labels=("a", "remove"), revision=4)
    storage = FakeStorage(tickets=(original,))
    clock = FixedClock(NEXT)
    app = WyrdApplication(storage, clock)

    result = app.edit_ticket(
        1,
        EditResourceRequest(
            title=" New ",
            body="new\rbody",
            add_labels=("z", "a"),
            remove_labels=("remove",),
            expected_revision=4,
        ),
    )

    assert result.revision == 5
    assert result.updated_at == NEXT
    assert result.title == "New"
    assert result.body == "new\nbody"
    assert result.labels == ("a", "z")
    assert storage.writes == [("update_ticket", 1)]
    assert clock.calls == 1


def test_edit_noop_does_not_write_advance_revision_or_read_clock() -> None:
    original = ticket(1, title="Same", labels=("bug",), revision=4)
    storage = FakeStorage(tickets=(original,))
    clock = FixedClock(NEXT)
    result = WyrdApplication(storage, clock).edit_ticket(
        1,
        EditResourceRequest(
            title="Same", add_labels=("bug",), remove_labels=("absent",)
        ),
    )
    assert result.revision == 4
    assert result.updated_at == NOW
    assert storage.writes == []
    assert clock.calls == 0


def test_expected_revision_is_checked_before_noop() -> None:
    storage = FakeStorage(tickets=(ticket(1, title="Same", revision=4),))
    app = WyrdApplication(storage, FixedClock())
    with pytest.raises(RevisionConflictError):
        app.edit_ticket(
            1,
            EditResourceRequest(title="Same", expected_revision=3),
        )
    assert storage.writes == []


def test_terminal_ticket_and_inactive_task_reject_even_noop_edits() -> None:
    parent = ticket(1, status=ResourceStatus.DISMISSED)
    storage = FakeStorage(tickets=(parent,), tasks=(task(1, 1),))
    app = WyrdApplication(storage, FixedClock())
    with pytest.raises(ResourceNotActiveError):
        app.edit_ticket(1, EditResourceRequest(title=parent.title))
    with pytest.raises(ResourceNotActiveError):
        app.edit_task(TaskIdentity(ticket_id=1, number=1), EditResourceRequest(body=""))
    assert storage.writes == []


def test_edit_request_requires_explicit_change_and_disjoint_labels() -> None:
    storage = FakeStorage(tickets=(ticket(1),))
    app = WyrdApplication(storage, FixedClock())
    with pytest.raises(DomainValidationError):
        app.edit_ticket(1, EditResourceRequest())
    with pytest.raises(DomainValidationError):
        app.edit_ticket(
            1,
            EditResourceRequest(add_labels=("bug",), remove_labels=("bug",)),
        )
    assert storage.write_transactions == 0


def test_task_edit_updates_only_task_not_parent() -> None:
    parent = ticket(1, revision=5)
    original = task(1, 1, revision=2)
    storage = FakeStorage(tickets=(parent,), tasks=(original,))
    result = WyrdApplication(storage, FixedClock(NEXT)).edit_task(
        original.identity, EditResourceRequest(title="Changed")
    )
    assert result.revision == 3
    assert storage.tickets[1].revision == 5
    assert storage.writes == [("update_task", "1.1")]


def test_all_multiresource_reads_use_exactly_one_read_transaction() -> None:
    storage = FakeStorage(tickets=(ticket(1), ticket(2)))
    app = WyrdApplication(storage, FixedClock())
    app.list_tickets(TicketListFilter(status="all"))
    assert storage.read_transactions == 1
    assert storage.write_transactions == 0
    assert storage.calls[0].startswith("read.enter")
    assert storage.calls[-1] == "read.exit"


def test_every_effective_normal_mutation_records_at_most_one_write() -> None:
    storage = FakeStorage(tickets=(ticket(1),), tasks=(task(1, 1),))
    app = WyrdApplication(storage, FixedClock(NEXT))
    app.edit_task(TaskIdentity(ticket_id=1, number=1), EditResourceRequest(title="X"))
    assert len(storage.writes) == 1
