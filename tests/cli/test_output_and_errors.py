from __future__ import annotations

import json
import re

import pytest

from tests.support.factories import task, ticket
from wyrd_cli.application.dto import DoctorProblemDTO
from wyrd_cli.application.storage import StructuralScan
from wyrd_cli.domain.models import ResourceStatus

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

TICKET_KEYS = {
    "active",
    "active_blocked_by",
    "active_blocking",
    "blocked_by",
    "blocking",
    "body",
    "closed_at",
    "created_at",
    "id",
    "is_blocked",
    "labels",
    "revision",
    "status",
    "tasks",
    "tasks_summary",
    "title",
    "type",
    "updated_at",
}
TASK_KEYS = {
    "active",
    "active_blocked_by",
    "active_blocking",
    "blocked_by",
    "blocking",
    "body",
    "closed_at",
    "created_at",
    "id",
    "is_blocked",
    "labels",
    "number",
    "revision",
    "status",
    "ticket_id",
    "title",
    "type",
    "updated_at",
}


def test_json_project_status_resource_and_list_shapes(runner, cli_factory, storage) -> None:
    storage.tickets = {1: ticket(1, body="body é", labels=("bug",))}
    storage.tasks = {1: {1: task(1, 1, labels=("task",))}}

    viewed = runner.invoke(cli_factory(), ["ticket", "view", "1", "--json"])
    assert viewed.exit_code == 0
    ticket_value = json.loads(viewed.stdout)
    assert set(ticket_value) == TICKET_KEYS
    assert ticket_value["id"] == 1
    assert ticket_value["closed_at"] is None
    assert ticket_value["body"] == "body é"
    assert list(ticket_value) == sorted(ticket_value)
    assert viewed.stderr == ""
    assert viewed.stdout.count("\n") == 1

    tasks = runner.invoke(cli_factory(), ["task", "list", "--ticket", "1", "--json"])
    task_values = json.loads(tasks.stdout)
    assert isinstance(task_values, list)
    assert set(task_values[0]) == TASK_KEYS
    assert task_values[0]["id"] == "1.1"
    assert isinstance(task_values[0]["id"], str)

    status = json.loads(runner.invoke(cli_factory(), ["status", "--json"]).stdout)
    assert set(status) == {"labels", "project", "tasks", "tickets"}
    assert set(status["project"]) == {"created_at", "name", "root", "schema_version"}
    assert set(status["tickets"]) == {"blocked", "completed", "dismissed", "open", "total"}
    assert set(status["tasks"]) == {
        "active_open",
        "blocked",
        "completed",
        "dismissed",
        "inactive_open",
        "open",
        "total",
    }

    labels = json.loads(runner.invoke(cli_factory(), ["label", "list", "--json"]).stdout)
    assert [item["name"] for item in labels] == ["bug", "task"]
    assert set(labels[0]) == {"name", "task_count", "ticket_count", "total_count"}


def test_json_mutation_and_dependency_commands_return_complete_objects(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1), 2: ticket(2)}
    storage.tasks = {1: {1: task(1, 1), 2: task(1, 2)}, 2: {}}
    app = cli_factory()

    commands = [
        ["ticket", "edit", "1", "--title", "Changed", "--json"],
        ["ticket", "dependency", "add", "1", "--blocked-by", "2", "--json"],
        ["ticket", "dependency", "remove", "1", "--blocked-by", "2", "--json"],
        ["ticket", "dependency", "list", "1", "--json"],
        ["task", "edit", "1.1", "--title", "Changed", "--json"],
        ["task", "dependency", "add", "1.2", "--blocked-by", "1.1", "--json"],
        ["task", "dependency", "remove", "1.2", "--blocked-by", "1.1", "--json"],
        ["task", "dependency", "list", "1.1", "--json"],
    ]
    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (command, result.stderr)
        value = json.loads(result.stdout)
        expected = TASK_KEYS if command[0] == "task" else TICKET_KEYS
        assert set(value) == expected
        assert result.stderr == ""
        assert result.stdout.count("\n") == 1


def test_human_lists_have_fixed_columns_and_preserve_order(runner, cli_factory, storage) -> None:
    storage.tickets = {4: ticket(4), 1: ticket(1)}
    storage.tasks = {1: {3: task(1, 3), 1: task(1, 1)}, 4: {}}
    tickets = runner.invoke(cli_factory(), ["ticket", "list"])
    assert tickets.exit_code == 0
    assert "ID | status | title | labels | blocked | updated time" in tickets.stdout
    assert tickets.stdout.index("1 | open") < tickets.stdout.index("4 | open")
    assert ANSI.search(tickets.stdout) is None

    tasks = runner.invoke(cli_factory(), ["task", "list", "--ticket", "1"])
    assert "ID | status | active | title | labels | blocked | updated time" in tasks.stdout
    assert tasks.stdout.index("1.1 | open") < tasks.stdout.index("1.3 | open")
    assert ANSI.search(tasks.stdout) is None


def test_non_tty_view_includes_metadata_then_complete_unstyled_body(
    runner, cli_factory, storage
) -> None:
    body = "# Heading\n```text\n---\n```\ntrailing"
    storage.tickets = {1: ticket(1, body=body)}
    storage.tasks = {1: {}}
    result = runner.invoke(cli_factory(), ["ticket", "view", "1"])
    assert result.exit_code == 0
    assert "ID: 1" in result.stdout
    assert "Status: open" in result.stdout
    assert "Body:\n" + body in result.stdout
    assert result.stdout.endswith(body)
    assert ANSI.search(result.stdout) is None


def test_human_dependency_output_has_both_directions_status_and_effectiveness(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {
        1: ticket(1, blocked_by=(2,)),
        2: ticket(2),
        3: ticket(3, blocked_by=(1,), status=ResourceStatus.DISMISSED),
    }
    storage.tasks = {1: {}, 2: {}, 3: {}}
    result = runner.invoke(cli_factory(), ["ticket", "dependency", "list", "1"])
    assert result.exit_code == 0
    assert "Blocked by:" in result.stdout
    assert "Blocking:" in result.stdout
    assert "ID | status | effective" in result.stdout
    assert "2 | open | yes" in result.stdout
    assert "3 | dismissed | no" in result.stdout


def test_application_errors_use_only_stderr_and_expected_exit(runner, cli_factory) -> None:
    result = runner.invoke(cli_factory(), ["ticket", "view", "99", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload == {
        "error": {
            "code": "ticket_not_found",
            "details": {"ticket_id": 99},
            "message": "Ticket 99 was not found.",
        }
    }


def test_doctor_result_exit_and_stream_contract(runner, cli_factory, storage) -> None:
    healthy = runner.invoke(cli_factory(), ["doctor", "--json"])
    assert healthy.exit_code == 0
    assert json.loads(healthy.stdout) == {"healthy": True, "problems": []}
    assert healthy.stderr == ""

    storage.scan_override = StructuralScan(
        project=storage.project,
        problems=(
            DoctorProblemDTO(
                path="project.yaml",
                code="broken",
                message="Broken.",
                details={"x": 1},
            ),
        ),
    )
    unhealthy = runner.invoke(cli_factory(), ["doctor", "--json"])
    assert unhealthy.exit_code == 1
    assert json.loads(unhealthy.stdout)["healthy"] is False
    assert unhealthy.stderr == ""


def test_json_and_non_tty_never_emit_ansi(runner, cli_factory, storage) -> None:
    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {}}
    for args in (
        ["ticket", "view", "1", "--json"],
        ["ticket", "view", "1"],
        ["ticket", "list", "--no-color"],
    ):
        result = runner.invoke(cli_factory(), args, color=True)
        assert ANSI.search(result.stdout + result.stderr) is None


def test_no_color_environment_and_flag_disable_tty_styling(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {}}
    tty_app = cli_factory(is_tty=lambda _: True)

    colored = runner.invoke(tty_app, ["ticket", "list"])
    assert ANSI.search(colored.stdout) is not None

    flag = runner.invoke(tty_app, ["ticket", "list", "--no-color"])
    assert ANSI.search(flag.stdout) is None

    environment = runner.invoke(tty_app, ["ticket", "list"], env={"NO_COLOR": "1"})
    assert ANSI.search(environment.stdout) is None


def test_lock_timeout_reaches_application_for_read_and_write(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {}}
    assert runner.invoke(cli_factory(), ["status", "--lock-timeout", "0"]).exit_code == 0
    assert "read.enter:0.0" in storage.calls
    assert runner.invoke(
        cli_factory(),
        ["ticket", "edit", "1", "--title", "New", "--lock-timeout", "0"],
    ).exit_code == 0
    assert "write.enter:0.0" in storage.calls
