from __future__ import annotations

from datetime import UTC, datetime

from wyrd_cli.domain.models import ResourceStatus, Task, TaskIdentity, Ticket

NOW = datetime(2026, 8, 5, 18, 59, 36, tzinfo=UTC)
LATER = datetime(2026, 8, 5, 19, 0, 0, tzinfo=UTC)


def ticket(
    ticket_id: int,
    *,
    title: str | None = None,
    body: str = "",
    status: ResourceStatus = ResourceStatus.OPEN,
    labels: tuple[str, ...] = (),
    blocked_by: tuple[int, ...] = (),
    revision: int = 1,
) -> Ticket:
    closed_at = None if status is ResourceStatus.OPEN else LATER
    return Ticket(
        id=ticket_id,
        revision=revision,
        title=title or f"Ticket {ticket_id}",
        body=body,
        status=status,
        labels=labels,
        blocked_by=blocked_by,
        created_at=NOW,
        updated_at=closed_at or NOW,
        closed_at=closed_at,
    )


def task(
    ticket_id: int,
    number: int,
    *,
    title: str | None = None,
    body: str = "",
    status: ResourceStatus = ResourceStatus.OPEN,
    labels: tuple[str, ...] = (),
    blocked_by: tuple[int, ...] = (),
    revision: int = 1,
) -> Task:
    closed_at = None if status is ResourceStatus.OPEN else LATER
    return Task(
        identity=TaskIdentity(ticket_id=ticket_id, number=number),
        revision=revision,
        title=title or f"Task {ticket_id}.{number}",
        body=body,
        status=status,
        labels=labels,
        blocked_by=blocked_by,
        created_at=NOW,
        updated_at=closed_at or NOW,
        closed_at=closed_at,
    )
