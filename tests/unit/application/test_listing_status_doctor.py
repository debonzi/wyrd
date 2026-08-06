from __future__ import annotations

import pytest

from wyrd_cli.application.dto import (
    DoctorProblemDTO,
    TaskListFilter,
    TicketListFilter,
)
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.application.storage import LocatedTask, LocatedTicket, StructuralScan
from wyrd_cli.domain.errors import CorruptDataError, DomainValidationError, TicketNotFoundError
from wyrd_cli.domain.models import ResourceStatus
from tests.support.factories import task, ticket
from tests.support.fake_storage import FakeStorage, FixedClock


def test_ticket_list_defaults_open_orders_and_returns_complete_projections() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(9, status=ResourceStatus.DISMISSED),
            ticket(4, labels=("bug",)),
            ticket(1, labels=("bug", "p0")),
        ),
        tasks=(task(1, 2), task(1, 1)),
    )
    result = WyrdApplication(storage, FixedClock()).list_tickets()
    assert [item.id for item in result] == [1, 4]
    assert result[0].tasks == ("1.1", "1.2")
    assert result[0].tasks_summary.total == 2
    assert result[0].type == "ticket"


def test_ticket_label_filter_uses_and_semantics_and_unused_is_empty() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, labels=("bug", "p0")),
            ticket(2, labels=("bug",)),
        )
    )
    app = WyrdApplication(storage, FixedClock())
    assert [item.id for item in app.list_tickets(
        TicketListFilter(status="all", labels=("p0", "bug", "bug"))
    )] == [1]
    assert app.list_tickets(TicketListFilter(labels=("unused",))) == ()


def test_ticket_text_filter_uses_nfc_casefold_literal_matching() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, title="Cafe\u0301 startup", body="Markdown"),
            ticket(2, title="Other", body="A STRASSE detail"),
        )
    )
    app = WyrdApplication(storage, FixedClock())
    assert [item.id for item in app.list_tickets(TicketListFilter(text="CAFÉ"))] == [1]
    assert [item.id for item in app.list_tickets(TicketListFilter(text="straße"))] == [2]
    assert app.list_tickets(TicketListFilter(text="cafe")) == ()
    with pytest.raises(DomainValidationError):
        app.list_tickets(TicketListFilter(text="   "))


def test_task_list_is_scoped_sorted_and_open_includes_inactive() -> None:
    storage = FakeStorage(
        tickets=(ticket(1, status=ResourceStatus.DISMISSED), ticket(2)),
        tasks=(
            task(1, 8, status=ResourceStatus.COMPLETED),
            task(1, 3),
            task(2, 1),
        ),
    )
    app = WyrdApplication(storage, FixedClock())
    all_results = app.list_tasks(1)
    assert [item.id for item in all_results] == ["1.3", "1.8"]
    open_results = app.list_tasks(1, TaskListFilter(status=ResourceStatus.OPEN))
    assert [item.id for item in open_results] == ["1.3"]
    assert not open_results[0].active
    with pytest.raises(TicketNotFoundError):
        app.list_tasks(99)


def test_label_aggregation_includes_terminal_and_inactive_resources() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, labels=("bug",)),
            ticket(2, status=ResourceStatus.DISMISSED, labels=("bug", "old")),
        ),
        tasks=(
            task(1, 1, status=ResourceStatus.COMPLETED, labels=("bug", "task")),
            task(2, 1, labels=("task",)),
        ),
    )
    result = WyrdApplication(storage, FixedClock()).list_labels()
    assert [item.name for item in result] == ["bug", "old", "task"]
    assert result[0].model_dump() == {
        "name": "bug",
        "ticket_count": 2,
        "task_count": 1,
        "total_count": 3,
    }
    assert result[2].total_count == 2


def test_project_status_equations_activity_blocking_and_distinct_labels() -> None:
    storage = FakeStorage(
        tickets=(
            ticket(1, blocked_by=(2,), labels=("bug",)),
            ticket(2),
            ticket(3, status=ResourceStatus.DISMISSED, labels=("old",)),
            ticket(4, status=ResourceStatus.COMPLETED),
        ),
        tasks=(
            task(1, 1, blocked_by=(2,), labels=("task",)),
            task(1, 2),
            task(3, 1, labels=("old",)),
            task(4, 1, status=ResourceStatus.COMPLETED),
            task(4, 2, status=ResourceStatus.DISMISSED),
        ),
    )
    status = WyrdApplication(storage, FixedClock()).project_status()
    assert status.tickets.model_dump() == {
        "total": 4,
        "open": 2,
        "completed": 1,
        "dismissed": 1,
        "blocked": 1,
    }
    assert status.tasks.model_dump() == {
        "total": 5,
        "open": 3,
        "completed": 1,
        "dismissed": 1,
        "active_open": 2,
        "inactive_open": 1,
        "blocked": 1,
    }
    assert status.labels.distinct == 3
    assert storage.read_transactions == 1


def test_doctor_combines_structural_and_domain_problems_in_stable_order_read_only() -> None:
    one = ticket(1, status=ResourceStatus.COMPLETED, blocked_by=(2,))
    two = ticket(2, blocked_by=(1,))
    open_child = task(1, 1, blocked_by=(2,))
    storage = FakeStorage(tickets=(one, two), tasks=(open_child,))
    storage.scan_override = StructuralScan(
        project=storage.project,
        tickets=(
            LocatedTicket(path="tickets/2/ticket.md", ticket=two),
            LocatedTicket(path="tickets/1/ticket.md", ticket=one),
        ),
        tasks=(LocatedTask(path="tickets/1/tasks/1.md", task=open_child),),
        problems=(
            DoctorProblemDTO(
                path="project.yaml",
                code="structural_problem",
                message="broken",
                details={},
            ),
        ),
    )
    report = WyrdApplication(storage, FixedClock()).doctor()
    assert not report.healthy
    ordering = [(problem.path, problem.code) for problem in report.problems]
    assert ordering == sorted(ordering)
    assert {problem.code for problem in report.problems} >= {
        "structural_problem",
        "dependency_cycle",
        "dependency_target_not_found",
        "invalid_resource_activity",
    }
    assert storage.writes == []
    assert "structural_scan" in storage.calls
    assert "list_tickets" not in storage.calls


def test_ordinary_commands_reject_domain_corruption_without_write() -> None:
    storage = FakeStorage(tickets=(ticket(1, blocked_by=(99,)),))
    with pytest.raises(CorruptDataError):
        WyrdApplication(storage, FixedClock()).project_status()
    assert storage.writes == []
