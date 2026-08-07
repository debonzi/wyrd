from __future__ import annotations

import re
from pathlib import Path

import yaml
from typer.testing import CliRunner

import wyrd_cli
from tests.integration.conftest import assert_json_document
from wyrd_cli.bootstrap import create_cli

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "wyrd"
SKILL_FILE = SKILL / "SKILL.md"
README_FILE = ROOT / "README.md"

EXPECTED_COMMANDS = {
    "wyrd doctor",
    "wyrd init",
    "wyrd label list",
    "wyrd status",
    "wyrd task complete",
    "wyrd task create",
    "wyrd task dependency add",
    "wyrd task dependency list",
    "wyrd task dependency remove",
    "wyrd task dismiss",
    "wyrd task edit",
    "wyrd task list",
    "wyrd task view",
    "wyrd ticket complete",
    "wyrd ticket create",
    "wyrd ticket dependency add",
    "wyrd ticket dependency list",
    "wyrd ticket dependency remove",
    "wyrd ticket dismiss",
    "wyrd ticket edit",
    "wyrd ticket list",
    "wyrd ticket view",
}
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


def _frontmatter() -> tuple[dict[str, object], str]:
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    encoded, body = text[4:].split("\n---\n", 1)
    value = yaml.safe_load(encoded)
    assert isinstance(value, dict)
    return value, body


def _ok(result):
    assert result.returncode == 0, (result.args, result.stdout, result.stderr)
    assert result.stderr == ""
    return assert_json_document(result.stdout)


def _managed_bytes(project: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in sorted((project / ".wyrd").rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_skill_package_follows_agent_skills_metadata_and_progressive_disclosure() -> None:
    metadata, body = _frontmatter()

    assert set(metadata) == {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
    }
    assert metadata["name"] == SKILL.name == "wyrd"
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(metadata["name"]))
    assert 1 <= len(str(metadata["name"])) <= 64
    assert 1 <= len(str(metadata["description"])) <= 1024
    assert 1 <= len(str(metadata["compatibility"])) <= 500
    assert metadata["license"] == "MIT"
    assert metadata["metadata"] == {"version": wyrd_cli.__version__}
    compatibility = str(metadata["compatibility"])
    assert "wyrd-cli 0.1.x" in compatibility
    assert "--summary" in compatibility
    assert "python" not in compatibility.lower()

    assert len(SKILL_FILE.read_text(encoding="utf-8").splitlines()) < 500
    assert "Never read or edit `.wyrd/` as an API" in body
    assert "--expected-revision" in body
    assert "--yes --json" in body
    for command in (
        "wyrd status --json",
        "wyrd ticket list --status open --summary --json",
        "wyrd ticket list --status open --label bug --text startup --summary --json",
        "wyrd task list --ticket 3 --status open --summary --json",
        "wyrd ticket view 3 --json",
        "wyrd task view 3.2 --json",
    ):
        assert command in body

    relative_links = {
        target
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", body)
        if "://" not in target
    }
    assert relative_links == {
        "references/commands.md",
        "references/errors.md",
        "references/lifecycle.md",
    }
    assert all(".." not in Path(target).parts for target in relative_links)
    assert all((SKILL / target).is_file() for target in relative_links)

    assert not (SKILL / "scripts" / "context.py").exists()
    assert not (SKILL / "scripts").exists()
    for documentation in (SKILL_FILE, README_FILE):
        text = documentation.read_text(encoding="utf-8")
        assert "context.py" not in text
        assert "<skill-root>" not in text


def test_skill_command_reference_covers_cli_leaf_commands_and_options() -> None:
    reference = (SKILL / "references" / "commands.md").read_text(encoding="utf-8")
    matches = list(
        re.finditer(
            r"^### `(wyrd [^`]+)`\n\n(.*?)(?=^### `|\Z)",
            reference,
            re.MULTILINE | re.DOTALL,
        )
    )
    documented = {match.group(1) for match in matches}
    assert documented == EXPECTED_COMMANDS

    common_options = {"--json", "--lock-timeout", "--no-color"}
    options_by_command: dict[str, set[str]] = {}
    runner = CliRunner()
    app = create_cli()
    for match in matches:
        command, section = match.groups()
        result = runner.invoke(app, [*command.split()[1:], "--help"])
        assert result.exit_code == 0, (command, result.stdout, result.stderr)

        syntax = re.search(r"```text\n(.*?)\n```", section, re.DOTALL)
        assert syntax is not None, command
        expected_options = set(re.findall(r"--[a-z][a-z-]*", syntax.group(1)))
        if "[common options]" in syntax.group(1):
            expected_options |= common_options
        actual_options = set(re.findall(r"--[a-z][a-z-]*", result.stdout)) - {
            "--help"
        }
        assert actual_options == expected_options, command
        options_by_command[command] = actual_options

    assert {
        command for command, options in options_by_command.items() if "--summary" in options
    } == {"wyrd ticket list", "wyrd task list"}


def test_skill_native_summary_workflow_filters_orders_and_never_writes(
    wyrd_process, project_dir: Path
) -> None:
    _ok(wyrd_process.run(project_dir, "init", "--name", "Skill test", "--json"))
    secret_body = "large-body-marker-" + "x" * 10_000
    _ok(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Startup regression",
            "--body",
            secret_body,
            "--label",
            "bug",
            "--json",
        )
    )
    _ok(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Open blocker",
            "--json",
        )
    )
    _ok(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Finished work",
            "--json",
        )
    )
    _ok(
        wyrd_process.run(
            project_dir,
            "ticket",
            "complete",
            "3",
            "--yes",
            "--json",
        )
    )
    _ok(
        wyrd_process.run(
            project_dir,
            "ticket",
            "dependency",
            "add",
            "1",
            "--blocked-by",
            "2",
            "--json",
        )
    )

    for title, label in (
        ("Measure baseline", "benchmark"),
        ("Historical measurement", None),
        ("Analyze results", None),
    ):
        arguments = [
            "task",
            "create",
            "--ticket",
            "1",
            "--title",
            title,
            "--body",
            secret_body,
        ]
        if label is not None:
            arguments.extend(("--label", label))
        arguments.append("--json")
        _ok(wyrd_process.run(project_dir, *arguments))
    _ok(
        wyrd_process.run(
            project_dir,
            "task",
            "complete",
            "1.2",
            "--yes",
            "--json",
        )
    )
    _ok(
        wyrd_process.run(
            project_dir,
            "task",
            "dependency",
            "add",
            "1.3",
            "--blocked-by",
            "1.1",
            "--json",
        )
    )

    full_ticket = _ok(
        wyrd_process.run(project_dir, "ticket", "view", "1", "--json")
    )
    full_task = _ok(wyrd_process.run(project_dir, "task", "view", "1.3", "--json"))
    before = _managed_bytes(project_dir)

    _ok(wyrd_process.run(project_dir, "status", "--json"))
    tickets_result = wyrd_process.run(
        project_dir,
        "ticket",
        "list",
        "--status",
        "open",
        "--summary",
        "--json",
    )
    tickets = _ok(tickets_result)
    assert [item["id"] for item in tickets] == [1, 2]
    assert all(set(item) == TICKET_SUMMARY_KEYS for item in tickets)
    assert tickets[0]["revision"] == full_ticket["revision"]
    assert tickets[0]["active_blocked_by"] == [2]
    assert tickets[0]["is_blocked"] is True
    assert tickets[1]["active_blocking"] == [1]
    assert {
        "active",
        "blocked_by",
        "blocking",
        "body",
        "closed_at",
        "created_at",
        "tasks",
        "updated_at",
    }.isdisjoint(tickets[0])
    assert secret_body not in tickets_result.stdout

    filtered = _ok(
        wyrd_process.run(
            project_dir,
            "ticket",
            "list",
            "--status",
            "open",
            "--label",
            "bug",
            "--text",
            "startup",
            "--summary",
            "--json",
        )
    )
    assert [item["id"] for item in filtered] == [1]

    tasks_result = wyrd_process.run(
        project_dir,
        "task",
        "list",
        "--ticket",
        "1",
        "--status",
        "open",
        "--summary",
        "--json",
    )
    tasks = _ok(tasks_result)
    assert [item["id"] for item in tasks] == ["1.1", "1.3"]
    assert all(set(item) == TASK_SUMMARY_KEYS for item in tasks)
    assert "1.2" not in {item["id"] for item in tasks}
    assert tasks[0]["active_blocking"] == ["1.3"]
    assert tasks[1]["revision"] == full_task["revision"]
    assert tasks[1]["active_blocked_by"] == ["1.1"]
    assert tasks[1]["is_blocked"] is True
    assert {
        "blocked_by",
        "blocking",
        "body",
        "closed_at",
        "created_at",
        "updated_at",
    }.isdisjoint(tasks[1])
    assert secret_body not in tasks_result.stdout
    assert _managed_bytes(project_dir) == before
