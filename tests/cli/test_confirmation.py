from __future__ import annotations

import json

import pytest

from tests.support.factories import task, ticket
from wyrd_cli.domain.models import ResourceStatus


def test_non_tty_and_json_real_transitions_require_yes(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {1: task(1, 1)}}

    for args in (
        ["ticket", "complete", "1"],
        ["task", "dismiss", "1.1", "--json"],
    ):
        result = runner.invoke(cli_factory(), args)
        assert result.exit_code == 1
        output = result.stderr
        if "--json" in args:
            assert json.loads(output)["error"]["code"] == "confirmation_required"
        else:
            assert "confirmation_required" in output
        assert "Proceed?" not in result.stdout + result.stderr
    assert storage.writes == []


def test_yes_confirms_without_prompt_and_never_bypasses_rules(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {1: task(1, 1)}}
    result = runner.invoke(
        cli_factory(confirm=lambda _: pytest.fail("must not prompt")),
        ["task", "dismiss", "1.1", "--yes", "--json"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "dismissed"

    blocked_storage_ticket = ticket(2, blocked_by=(1,))
    storage.tickets[2] = blocked_storage_ticket
    storage.tasks[2] = {}
    blocked = runner.invoke(
        cli_factory(), ["ticket", "complete", "2", "--yes", "--json"]
    )
    assert blocked.exit_code == 1
    assert json.loads(blocked.stderr)["error"]["code"] == "blocked_by_open_dependency"


def test_tty_prompt_happens_after_preflight_lock_release_and_passes_revision(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1, revision=2)}
    storage.tasks = {1: {}}
    observed: list[str] = []

    def confirm(message: str) -> bool:
        assert storage.calls[-1] == "read.exit"
        observed.append(message)
        return True

    result = runner.invoke(
        cli_factory(is_tty=lambda _: True, confirm=confirm),
        ["ticket", "complete", "1"],
    )
    assert result.exit_code == 0
    assert observed == ["Proceed?"]
    assert "Resource: 1" in result.stdout
    assert "Title: Ticket 1" in result.stdout
    assert "Requested status: completed" in result.stdout
    assert storage.writes == [("update_ticket", 1)]


def test_declining_prompt_is_operation_cancelled_without_mutation(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {}}
    result = runner.invoke(
        cli_factory(is_tty=lambda _: True, confirm=lambda _: False),
        ["ticket", "dismiss", "1"],
    )
    assert result.exit_code == 1
    assert "operation_cancelled" in result.stderr
    assert storage.writes == []


def test_ticket_dismiss_preview_warns_about_open_tasks(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1)}
    storage.tasks = {1: {1: task(1, 1), 2: task(1, 2)}}
    result = runner.invoke(
        cli_factory(is_tty=lambda _: True, confirm=lambda _: False),
        ["ticket", "dismiss", "1"],
    )
    assert "2 open task(s) will become inactive" in result.stdout


def test_confirmation_revision_is_revalidated_without_retry(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {1: ticket(1, revision=2)}
    storage.tasks = {1: {}}

    def concurrent_change(_: str) -> bool:
        storage.tickets[1] = ticket(1, title="Changed", revision=3)
        return True

    result = runner.invoke(
        cli_factory(is_tty=lambda _: True, confirm=concurrent_change),
        ["ticket", "complete", "1", "--json"],
    )
    # JSON mode is never prompt-eligible, even when streams are marked as TTY.
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "confirmation_required"

    result = runner.invoke(
        cli_factory(is_tty=lambda _: True, confirm=concurrent_change),
        ["ticket", "complete", "1"],
    )
    assert result.exit_code == 1
    assert "revision_conflict" in result.stderr
    assert storage.writes == []


def test_idempotent_terminal_request_and_opposite_status_never_prompt(
    runner, cli_factory, storage
) -> None:
    storage.tickets = {
        1: ticket(1, status=ResourceStatus.COMPLETED),
        2: ticket(2, status=ResourceStatus.DISMISSED),
    }
    storage.tasks = {1: {}, 2: {}}

    def forbidden(_: str) -> bool:
        raise AssertionError("terminal requests must not prompt")

    idempotent = runner.invoke(
        cli_factory(is_tty=lambda _: True, confirm=forbidden),
        ["ticket", "complete", "1", "--json"],
    )
    assert idempotent.exit_code == 0
    assert json.loads(idempotent.stdout)["status"] == "completed"
    assert storage.writes == []

    opposite = runner.invoke(
        cli_factory(is_tty=lambda _: True, confirm=forbidden),
        ["ticket", "complete", "2"],
    )
    assert opposite.exit_code == 1
    assert "conflict" in opposite.stderr
    assert storage.writes == []
