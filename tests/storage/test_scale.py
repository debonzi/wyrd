from __future__ import annotations

from pathlib import Path

from tests.storage.conftest import STORAGE_TIME
from tests.support.factories import task, ticket
from tests.support.fake_storage import FixedClock
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.infrastructure.filesystem import create_filesystem_storage
from wyrd_cli.infrastructure.filesystem.codec import encode_task, encode_ticket


def test_direct_scans_support_thousands_of_resources_without_indexes(
    initialized_project,
) -> None:
    root, _, _ = initialized_project
    tickets_dir = root / ".wyrd/tickets"
    count = 1_000
    for ticket_id in range(1, count + 1):
        directory = tickets_dir / str(ticket_id)
        tasks_dir = directory / "tasks"
        tasks_dir.mkdir(parents=True)
        (directory / "ticket.md").write_bytes(
            encode_ticket(ticket(ticket_id, title=f"Ticket {ticket_id}"))
        )
        (tasks_dir / "1.md").write_bytes(
            encode_task(task(ticket_id, 1, title=f"Task {ticket_id}.1"))
        )

    app = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    status = app.project_status()
    assert status.tickets.total == count
    assert status.tasks.total == count
    assert status.tickets.open == count
    assert status.tasks.active_open == count
    assert set(path.name for path in (root / ".wyrd").iterdir()) == {
        "project.yaml",
        "lock",
        "tickets",
    }
