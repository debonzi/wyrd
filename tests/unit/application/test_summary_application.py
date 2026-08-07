from __future__ import annotations

from wyrd_cli.application.dto import (
    TaskListFilter,
    TaskSummaryDTO,
    TasksSummaryDTO,
    TicketListFilter,
    TicketSummaryDTO,
)
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.models import ResourceStatus
from tests.support.factories import task, ticket
from tests.support.fake_storage import FakeStorage, FixedClock


TICKET_SUMMARY_KEYS = {
    "active_blocked_by",
    "active_blocking",
    "id",
    "is_blocked",
    "labels",
    "revision",
    "status",
    "tasks_summary",
    "title",
    "type",
}
TASK_SUMMARY_KEYS = {
    "active",
    "active_blocked_by",
    "active_blocking",
    "id",
    "is_blocked",
    "labels",
    "number",
    "revision",
    "status",
    "ticket_id",
    "title",
    "type",
}


def test_summary_dtos_have_exact_typed_shapes_and_effective_relations() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(7, blocked_by=(3,)),
            ticket(3, body="private ticket body", labels=("bug",), blocked_by=(5,)),
            ticket(5),
        ),
        tasks=(
            task(3, 3, body="private task body", blocked_by=(1,)),
            task(3, 1, labels=("first",)),
        ),
    )
    application = WyrdApplication(storage, FixedClock())

    ticket_result = application.list_ticket_summaries()
    assert all(isinstance(item, TicketSummaryDTO) for item in ticket_result)
    projected_ticket = next(item for item in ticket_result if item.id == 3)
    ticket_value = projected_ticket.model_dump()
    assert set(ticket_value) == TICKET_SUMMARY_KEYS
    assert projected_ticket.type == "ticket"
    assert isinstance(projected_ticket.id, int)
    assert isinstance(projected_ticket.revision, int)
    assert isinstance(projected_ticket.title, str)
    assert isinstance(projected_ticket.status, ResourceStatus)
    assert isinstance(projected_ticket.labels, tuple)
    assert isinstance(projected_ticket.tasks_summary, TasksSummaryDTO)
    assert TicketSummaryDTO.model_config["frozen"] is True
    assert projected_ticket.active_blocked_by == (5,)
    assert projected_ticket.active_blocking == (7,)
    assert projected_ticket.is_blocked is True
    assert projected_ticket.tasks_summary.model_dump() == {
        "total": 2,
        "open": 2,
        "completed": 0,
        "dismissed": 0,
        "active_open": 2,
        "inactive_open": 0,
    }
    assert {
        "body",
        "created_at",
        "updated_at",
        "closed_at",
        "blocked_by",
        "blocking",
        "tasks",
    }.isdisjoint(ticket_value)

    task_result = application.list_task_summaries(3)
    assert all(isinstance(item, TaskSummaryDTO) for item in task_result)
    assert [item.id for item in task_result] == ["3.1", "3.3"]
    first, blocked = task_result
    task_value = blocked.model_dump()
    assert set(task_value) == TASK_SUMMARY_KEYS
    assert blocked.type == "task"
    assert isinstance(blocked.id, str)
    assert isinstance(blocked.ticket_id, int)
    assert isinstance(blocked.number, int)
    assert isinstance(blocked.revision, int)
    assert isinstance(blocked.status, ResourceStatus)
    assert isinstance(blocked.labels, tuple)
    assert isinstance(blocked.active, bool)
    assert TaskSummaryDTO.model_config["frozen"] is True
    assert blocked.active_blocked_by == ("3.1",)
    assert first.active_blocking == ("3.3",)
    assert blocked.is_blocked is True
    assert {
        "body",
        "created_at",
        "updated_at",
        "closed_at",
        "blocked_by",
        "blocking",
    }.isdisjoint(task_value)


def test_summary_lists_preserve_empty_results_filters_defaults_and_ordering() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(
                9,
                body="needle archived",
                status=ResourceStatus.DISMISSED,
                labels=("bug", "p0"),
            ),
            ticket(4, body="NEEDLE details", labels=("bug", "p0")),
            ticket(1, title="Needle first", labels=("bug", "p0")),
            ticket(6),
        ),
        tasks=(
            task(1, 8, status=ResourceStatus.COMPLETED),
            task(1, 3),
        ),
    )
    application = WyrdApplication(storage, FixedClock())

    assert [item.id for item in application.list_ticket_summaries()] == [1, 4, 6]
    filtered = application.list_ticket_summaries(
        TicketListFilter(
            status="all",
            labels=("p0", "bug", "bug"),
            text="needle",
        )
    )
    assert [item.id for item in filtered] == [1, 4, 9]
    dismissed = application.list_ticket_summaries(
        TicketListFilter(status=ResourceStatus.DISMISSED)
    )
    assert [item.id for item in dismissed] == [9]
    assert application.list_ticket_summaries(
        TicketListFilter(labels=("unused",))
    ) == ()

    assert [item.id for item in application.list_task_summaries(1)] == ["1.3", "1.8"]
    open_tasks = application.list_task_summaries(
        1, TaskListFilter(status=ResourceStatus.OPEN)
    )
    assert [item.id for item in open_tasks] == ["1.3"]
    assert application.list_task_summaries(6) == ()

    empty = WyrdApplication(FakeStorage(), FixedClock())
    assert empty.list_ticket_summaries() == ()


def test_summary_lists_are_read_only_and_propagate_timeout() -> None:
    ticket_storage = FakeStorage(tickets=(ticket(1),))
    ticket_application = WyrdApplication(ticket_storage, FixedClock())
    ticket_application.list_ticket_summaries(lock_timeout=0.25)
    assert ticket_storage.calls.count("read.enter:0.25") == 1
    assert ticket_storage.read_transactions == 1
    assert ticket_storage.write_transactions == 0
    assert ticket_storage.writes == []

    task_storage = FakeStorage(tickets=(ticket(1),), tasks=(task(1, 1),))
    task_application = WyrdApplication(task_storage, FixedClock())
    task_application.list_task_summaries(1, lock_timeout=0.5)
    assert task_storage.calls.count("read.enter:0.5") == 1
    assert task_storage.read_transactions == 1
    assert task_storage.write_transactions == 0
    assert task_storage.writes == []
