from __future__ import annotations

import json
import re

from tests.support.factories import task, ticket
from wyrd_cli.domain.models import ResourceStatus

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

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


def test_tree_defaults_human_hierarchy_and_inactive_tasks(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {
        3: ticket(3, title="Archived", status=ResourceStatus.DISMISSED),
        2: ticket(2, title="Second"),
        1: ticket(1, title="First needle", labels=("bug",), blocked_by=(2,)),
    }
    storage.tasks = {
        1: {
            2: task(1, 2, title="Work", blocked_by=(1,)),
            1: task(1, 1, title="Done", status=ResourceStatus.COMPLETED),
        },
        2: {},
        3: {1: task(3, 1, title="Left open")},
    }

    result = runner.invoke(cli_factory(), ["tree"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert "ID | status | title | labels | blocked by | tasks" in result.stdout
    assert "1 | open | First needle | bug | 2 | 2/2" in result.stdout
    assert "├── 1.1 | completed | Done |  |  | -" in result.stdout
    assert "└── 1.2 | open | Work |  |  | -" in result.stdout
    assert "2 | open | Second |  |  | 0/0" in result.stdout
    assert "Archived" not in result.stdout
    lines = result.stdout.splitlines()
    separators = [
        index for index, line in enumerate(lines) if line and set(line) == {"-"}
    ]
    assert len(separators) == 1
    assert lines.index("└── 1.2 | open | Work |  |  | -") < separators[0]
    assert separators[0] < lines.index("2 | open | Second |  |  | 0/0")
    assert ANSI.search(result.stdout) is None

    all_tickets = runner.invoke(
        cli_factory(), ["tree", "--status", "all", "--task-status", "open"]
    )
    assert all_tickets.exit_code == 0
    assert "└── 3.1 | open (inactive) | Left open |  |  | -" in all_tickets.stdout


def test_tree_json_is_nested_compact_filtered_and_read_only(
    runner, cli_factory, storage
) -> None:
    large_body = "private-body-marker-é\n" * 1_000
    storage.tickets = {
        4: ticket(4, title="Other"),
        1: ticket(1, title="Needle", body=large_body, labels=("bug",)),
    }
    storage.tasks = {
        1: {
            5: task(1, 5, body=large_body, status=ResourceStatus.COMPLETED),
            2: task(1, 2, body=large_body),
        },
        4: {},
    }

    result = runner.invoke(
        cli_factory(),
        [
            "tree",
            "--label",
            "bug",
            "--text",
            "needle",
            "--task-status",
            "open",
            "--lock-timeout",
            "0.25",
            "--json",
        ],
        color=True,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    assert large_body not in result.stdout
    assert ANSI.search(result.stdout) is None
    value = json.loads(result.stdout)
    assert len(value) == 1
    assert set(value[0]) == {"tasks", "ticket"}
    assert set(value[0]["ticket"]) == TICKET_SUMMARY_KEYS
    assert [item["id"] for item in value[0]["tasks"]] == ["1.2"]
    assert set(value[0]["tasks"][0]) == TASK_SUMMARY_KEYS
    assert value[0]["ticket"]["tasks_summary"]["total"] == 2
    assert storage.calls.count("read.enter:0.25") == 1
    assert storage.write_transactions == 0
    assert storage.writes == []


def test_tree_empty_and_no_matching_task_output(runner, cli_factory, storage) -> None:
    empty = runner.invoke(cli_factory(), ["tree"])
    assert empty.exit_code == 0
    assert empty.stdout == "ID | status | title | labels | blocked by | tasks\n"

    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {1: task(1, 1)}}
    filtered = runner.invoke(
        cli_factory(), ["tree", "--task-status", "dismissed"]
    )
    assert filtered.exit_code == 0
    assert "1 | open | Ticket 1 |  |  | 0/1" in filtered.stdout
    assert "1.1 |" not in filtered.stdout
