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


def _assert_sorted_keys(value) -> None:
    if isinstance(value, dict):
        assert list(value) == sorted(value)
        for item in value.values():
            _assert_sorted_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_sorted_keys(item)


def test_summary_json_has_exact_shapes_while_default_json_stays_complete(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {
        1: ticket(1, body="ticket body é", labels=("bug",), blocked_by=(2,)),
        2: ticket(2),
        3: ticket(3),
    }
    storage.tasks = {
        1: {
            1: task(1, 1, body="task body é"),
            2: task(1, 2, blocked_by=(1,)),
        },
        2: {},
        3: {},
    }

    ticket_result = runner.invoke(
        cli_factory(), ["ticket", "list", "--summary", "--json"]
    )
    assert ticket_result.exit_code == 0
    assert ticket_result.stderr == ""
    assert ticket_result.stdout.count("\n") == 1
    ticket_values = json.loads(ticket_result.stdout)
    _assert_sorted_keys(ticket_values)
    assert set(ticket_values[0]) == TICKET_SUMMARY_KEYS
    assert ticket_values[0]["type"] == "ticket"
    assert isinstance(ticket_values[0]["id"], int)
    assert ticket_values[0]["active_blocked_by"] == [2]
    assert ticket_values[0]["tasks_summary"]["total"] == 2

    task_result = runner.invoke(
        cli_factory(),
        ["task", "list", "--ticket", "1", "--summary", "--json"],
    )
    assert task_result.exit_code == 0
    assert task_result.stderr == ""
    assert task_result.stdout.count("\n") == 1
    task_values = json.loads(task_result.stdout)
    _assert_sorted_keys(task_values)
    assert set(task_values[0]) == TASK_SUMMARY_KEYS
    assert task_values[0]["type"] == "task"
    assert isinstance(task_values[0]["id"], str)
    assert task_values[0]["active_blocking"] == ["1.2"]
    assert task_values[1]["active_blocked_by"] == ["1.1"]

    full_ticket = json.loads(
        runner.invoke(cli_factory(), ["ticket", "list", "--json"]).stdout
    )[0]
    assert full_ticket["body"] == "ticket body é"
    assert {
        "active",
        "blocked_by",
        "blocking",
        "body",
        "closed_at",
        "created_at",
        "tasks",
        "updated_at",
    } <= set(full_ticket)

    full_task = json.loads(
        runner.invoke(
            cli_factory(), ["task", "list", "--ticket", "1", "--json"]
        ).stdout
    )[0]
    assert full_task["body"] == "task body é"
    assert {"blocked_by", "blocking", "body", "created_at", "updated_at"} <= set(
        full_task
    )


def test_summary_cli_preserves_empty_arrays_filters_defaults_and_ordering(
    runner, cli_factory, storage
) -> None:
    empty_tickets = runner.invoke(
        cli_factory(), ["ticket", "list", "--summary", "--json"]
    )
    assert empty_tickets.exit_code == 0
    assert json.loads(empty_tickets.stdout) == []

    storage.tickets = {
        9: ticket(
            9,
            body="match archive",
            status=ResourceStatus.DISMISSED,
            labels=("bug", "p0"),
        ),
        4: ticket(4, body="MATCH body", labels=("bug", "p0")),
        1: ticket(1, title="Match first", labels=("bug", "p0")),
        6: ticket(6),
    }
    storage.tasks = {
        1: {
            8: task(1, 8, status=ResourceStatus.COMPLETED),
            3: task(1, 3),
        },
        4: {},
        6: {},
        9: {},
    }

    filtered = runner.invoke(
        cli_factory(),
        [
            "ticket",
            "list",
            "--status",
            "all",
            "--label",
            "bug",
            "--label",
            "p0",
            "--text",
            "match",
            "--summary",
            "--json",
        ],
    )
    assert filtered.exit_code == 0
    assert [item["id"] for item in json.loads(filtered.stdout)] == [1, 4, 9]

    default = runner.invoke(
        cli_factory(), ["ticket", "list", "--summary", "--json"]
    )
    assert [item["id"] for item in json.loads(default.stdout)] == [1, 4, 6]

    open_tasks = runner.invoke(
        cli_factory(),
        [
            "task",
            "list",
            "--ticket",
            "1",
            "--status",
            "open",
            "--summary",
            "--json",
        ],
    )
    assert [item["id"] for item in json.loads(open_tasks.stdout)] == ["1.3"]

    empty_tasks = runner.invoke(
        cli_factory(),
        ["task", "list", "--ticket", "6", "--summary", "--json"],
    )
    assert empty_tasks.exit_code == 0
    assert json.loads(empty_tasks.stdout) == []


def test_summary_without_json_preserves_the_exact_human_tables(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1, labels=("bug",))}
    storage.tasks = {1: {1: task(1, 1, labels=("task",))}}

    tickets = runner.invoke(cli_factory(), ["ticket", "list"])
    summarized_tickets = runner.invoke(
        cli_factory(), ["ticket", "list", "--summary"]
    )
    assert summarized_tickets.exit_code == tickets.exit_code == 0
    assert summarized_tickets.stdout == tickets.stdout
    assert summarized_tickets.stderr == tickets.stderr == ""

    tasks = runner.invoke(cli_factory(), ["task", "list", "--ticket", "1"])
    summarized_tasks = runner.invoke(
        cli_factory(), ["task", "list", "--ticket", "1", "--summary"]
    )
    assert summarized_tasks.exit_code == tasks.exit_code == 0
    assert summarized_tasks.stdout == tasks.stdout
    assert summarized_tasks.stderr == tasks.stderr == ""


def test_help_exposes_summary_only_on_ticket_and_task_list(runner, cli_factory) -> None:
    leaf_commands = (
        ("init",),
        ("status",),
        ("tree",),
        ("doctor",),
        ("ticket", "create"),
        ("ticket", "list"),
        ("ticket", "view"),
        ("ticket", "edit"),
        ("ticket", "complete"),
        ("ticket", "dismiss"),
        ("ticket", "dependency", "add"),
        ("ticket", "dependency", "remove"),
        ("ticket", "dependency", "list"),
        ("task", "create"),
        ("task", "list"),
        ("task", "view"),
        ("task", "edit"),
        ("task", "complete"),
        ("task", "dismiss"),
        ("task", "dependency", "add"),
        ("task", "dependency", "remove"),
        ("task", "dependency", "list"),
        ("label", "list"),
    )
    summary_commands = {("ticket", "list"), ("task", "list")}
    app = cli_factory()

    for command in leaf_commands:
        result = runner.invoke(app, [*command, "--help"])
        assert result.exit_code == 0, command
        plain_help = ANSI.sub("", result.stdout)
        assert ("--summary" in plain_help) is (command in summary_commands)

    rejected = runner.invoke(
        app, ["ticket", "view", "1", "--summary", "--json"]
    )
    assert rejected.exit_code == 2
    assert rejected.stdout == ""
    assert json.loads(rejected.stderr)["error"]["code"] == "usage_error"


def test_summary_streams_errors_locking_read_only_and_large_body_reduction(
    runner, cli_factory, storage
) -> None:
    large_body = "large-private-body-é\n" * 2_000
    storage.tickets = {1: ticket(1, body=large_body)}
    storage.tasks = {1: {1: task(1, 1, body=large_body)}}

    full = runner.invoke(cli_factory(), ["ticket", "list", "--json"], color=True)
    summary = runner.invoke(
        cli_factory(),
        [
            "ticket",
            "list",
            "--summary",
            "--json",
            "--lock-timeout",
            "0.25",
        ],
        color=True,
    )
    assert summary.exit_code == 0
    assert summary.stderr == ""
    assert summary.stdout.count("\n") == 1
    assert ANSI.search(summary.stdout + summary.stderr) is None
    assert "large-private-body-é" not in summary.stdout
    assert "large-private-body-é" in full.stdout
    assert len(summary.stdout) < len(full.stdout) // 10
    assert "read.enter:0.25" in storage.calls
    assert storage.write_transactions == 0
    assert storage.writes == []

    missing = runner.invoke(
        cli_factory(),
        ["task", "list", "--ticket", "99", "--summary", "--json"],
        color=True,
    )
    assert missing.exit_code == 1
    assert missing.stdout == ""
    assert json.loads(missing.stderr)["error"]["code"] == "ticket_not_found"
    assert ANSI.search(missing.stderr) is None
