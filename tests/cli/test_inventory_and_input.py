from __future__ import annotations

import json
from pathlib import Path

import pytest


EXPECTED_ROOT_COMMANDS = {"init", "status", "doctor", "ticket", "task", "label"}
EXPECTED_RESOURCE_COMMANDS = {
    "create",
    "list",
    "view",
    "edit",
    "complete",
    "dismiss",
    "dependency",
}
EXPECTED_DEPENDENCY_COMMANDS = {"add", "remove", "list"}


def _listed_commands(help_text: str, candidates: set[str]) -> set[str]:
    return {
        command
        for command in candidates
        if any(line.strip().startswith(command + " ") or line.strip() == command for line in help_text.splitlines())
    }


def test_exact_command_inventory_and_non_goals(runner, cli_factory) -> None:
    app = cli_factory()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    for command in EXPECTED_ROOT_COMMANDS:
        assert command in root.stdout
    for forbidden in ("issue", "subissue", "setup", "search", "migrate"):
        assert forbidden not in root.stdout
        assert runner.invoke(app, [forbidden]).exit_code == 2

    for group in ("ticket", "task"):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0
        for command in EXPECTED_RESOURCE_COMMANDS:
            assert command in result.stdout
        dependency = runner.invoke(app, [group, "dependency", "--help"])
        assert dependency.exit_code == 0
        for command in EXPECTED_DEPENDENCY_COMMANDS:
            assert command in dependency.stdout

    label = runner.invoke(app, ["label", "--help"])
    assert "list" in label.stdout


def test_help_and_version_need_no_application_or_project(runner) -> None:
    def forbidden_factory():
        raise AssertionError("application factory must not be called")

    from wyrd_cli.presentation import PresentationDependencies, create_app

    app = create_app(PresentationDependencies(application_factory=forbidden_factory))
    assert runner.invoke(app, ["--help"]).exit_code == 0
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert version.stdout == "0.1.0\n"


@pytest.mark.parametrize(
    "args",
    [
        ["ticket", "create", "--json"],
        ["task", "create", "--ticket", "1", "--json"],
        ["status", "--lock-timeout", "-1", "--json"],
        ["status", "--lock-timeout", "nan", "--json"],
        ["ticket", "edit", "1", "--expected-revision", "0", "--title", "x", "--json"],
        ["unknown", "--json"],
    ],
)
def test_json_parser_errors_are_single_stderr_envelopes(runner, cli_factory, args) -> None:
    result = runner.invoke(cli_factory(), args)
    assert result.exit_code == 2
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "details", "message"}
    assert payload["error"]["code"] == "usage_error"
    assert isinstance(payload["error"]["details"], dict)
    assert result.stderr.count("\n") == 1
    assert "Usage:" not in result.stderr


def test_option_value_containing_json_does_not_enable_json_parser_errors(
    runner, cli_factory
) -> None:
    result = runner.invoke(
        cli_factory(), ["ticket", "create", "--title=--json", "--unknown"]
    )
    assert result.exit_code == 2
    assert not result.stderr.lstrip().startswith("{")


@pytest.mark.parametrize(
    "args",
    [
        ["ticket", "view", "01", "--json"],
        ["ticket", "edit", "1", "--json"],
        ["task", "view", "1", "--json"],
        ["task", "list", "--ticket", "01", "--json"],
        ["task", "complete", "1", "--yes", "--json"],
        ["task", "dismiss", "1", "--yes", "--json"],
        ["task", "dependency", "add", "1", "--blocked-by", "1.1", "--json"],
        ["task", "dependency", "remove", "1.1", "--blocked-by", "1", "--json"],
        ["task", "dependency", "list", "1", "--json"],
    ],
)
def test_identity_and_explicit_edit_usage_errors_precede_discovery(
    runner, cli_factory, args
) -> None:
    result = runner.invoke(cli_factory(), args)
    assert result.exit_code == 2
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_body_literal_file_stdin_unicode_and_trailing_newline(
    runner, cli_factory, storage, tmp_path: Path
) -> None:
    body_path = tmp_path / "body.md"
    body_path.write_bytes("from file é\n\n".encode())

    literal = runner.invoke(
        cli_factory(),
        ["ticket", "create", "--title", "Literal", "--body", "literal", "--json"],
    )
    assert json.loads(literal.stdout)["body"] == "literal"

    from_file = runner.invoke(
        cli_factory(),
        ["ticket", "create", "--title", "File", "--body-file", str(body_path), "--json"],
    )
    assert json.loads(from_file.stdout)["body"] == "from file é\n\n"

    from_stdin = runner.invoke(
        cli_factory(),
        ["ticket", "create", "--title", "Stdin", "--body-file", "-", "--json"],
        input="stdin é\n",
    )
    assert json.loads(from_stdin.stdout)["body"] == "stdin é\n"


def test_body_exclusivity_and_file_failures_are_stable(
    runner, cli_factory, tmp_path: Path
) -> None:
    exclusive = runner.invoke(
        cli_factory(),
        [
            "ticket",
            "create",
            "--title",
            "Title",
            "--body",
            "one",
            "--body-file",
            "two",
            "--json",
        ],
    )
    assert exclusive.exit_code == 2
    assert json.loads(exclusive.stderr)["error"]["code"] == "usage_error"

    missing = runner.invoke(
        cli_factory(),
        ["ticket", "create", "--title", "Title", "--body-file", str(tmp_path / "missing"), "--json"],
    )
    assert missing.exit_code == 1
    assert json.loads(missing.stderr)["error"]["code"] == "validation_error"

    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    utf8 = runner.invoke(
        cli_factory(),
        ["ticket", "create", "--title", "Title", "--body-file", str(invalid), "--json"],
    )
    assert utf8.exit_code == 1
    assert json.loads(utf8.stderr)["error"]["code"] == "validation_error"


def test_edit_label_overlap_and_body_empty_presence(runner, cli_factory, storage) -> None:
    from tests.support.factories import ticket

    storage.tickets = {1: ticket(1, body="old")}
    storage.tasks = {1: {}}
    overlap = runner.invoke(
        cli_factory(),
        [
            "ticket",
            "edit",
            "1",
            "--add-label",
            "bug",
            "--remove-label",
            "bug",
            "--json",
        ],
    )
    assert overlap.exit_code == 2
    assert storage.writes == []

    empty = runner.invoke(
        cli_factory(), ["ticket", "edit", "1", "--body", "", "--json"]
    )
    assert empty.exit_code == 0
    assert json.loads(empty.stdout)["body"] == ""


def test_init_has_common_output_options_but_no_lock_timeout(runner, cli_factory) -> None:
    assert runner.invoke(cli_factory(), ["init", "--name", "New", "--json"]).exit_code == 0
    bad = runner.invoke(cli_factory(), ["init", "--lock-timeout", "0", "--json"])
    assert bad.exit_code == 2
    assert json.loads(bad.stderr)["error"]["code"] == "usage_error"
