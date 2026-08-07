from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

import wyrd_cli
from tests.integration.conftest import assert_json_document
from wyrd_cli.bootstrap import create_cli

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "wyrd"
SKILL_FILE = SKILL / "SKILL.md"
CONTEXT_HELPER = SKILL / "scripts" / "context.py"

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
TICKET_CONTEXT_KEYS = {
    "active_blocked_by",
    "active_blocking",
    "id",
    "is_blocked",
    "labels",
    "revision",
    "status",
    "tasks_summary",
    "title",
}
TASK_CONTEXT_KEYS = {
    "active",
    "active_blocked_by",
    "active_blocking",
    "id",
    "is_blocked",
    "labels",
    "revision",
    "status",
    "title",
}


def _frontmatter() -> tuple[dict[str, object], str]:
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    encoded, body = text[4:].split("\n---\n", 1)
    value = yaml.safe_load(encoded)
    assert isinstance(value, dict)
    return value, body


def _run_context(wyrd_process, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CONTEXT_HELPER),
            "--wyrd",
            str(wyrd_process.executable),
            *args,
        ],
        cwd=cwd,
        env=wyrd_process.env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15,
        check=False,
    )


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
    assert "wyrd-cli 0.1.x" in str(metadata["compatibility"])

    assert len(SKILL_FILE.read_text(encoding="utf-8").splitlines()) < 500
    assert "Never read or edit `.wyrd/` as an API" in body
    assert "--expected-revision" in body
    assert "--yes --json" in body

    relative_links = {
        target
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", body)
        if "://" not in target
    }
    assert relative_links == {
        "references/commands.md",
        "references/errors.md",
        "references/lifecycle.md",
        "scripts/context.py",
    }
    assert all(".." not in Path(target).parts for target in relative_links)
    assert all((SKILL / target).is_file() for target in relative_links)
    assert CONTEXT_HELPER.stat().st_mode & stat.S_IXUSR


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


def test_compact_context_helper_filters_projects_and_never_writes(
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
            "task",
            "create",
            "--ticket",
            "1",
            "--title",
            "Measure baseline",
            "--label",
            "benchmark",
            "--json",
        )
    )
    _ok(
        wyrd_process.run(
            project_dir,
            "task",
            "create",
            "--ticket",
            "1",
            "--title",
            "Historical measurement",
            "--json",
        )
    )
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
            "2",
            "--yes",
            "--json",
        )
    )
    before = _managed_bytes(project_dir)

    tickets_result = _run_context(wyrd_process, project_dir, "tickets")
    tickets = _ok(tickets_result)
    assert [item["id"] for item in tickets] == [1]
    assert set(tickets[0]) == TICKET_CONTEXT_KEYS
    assert secret_body not in tickets_result.stdout
    assert len(tickets_result.stdout.encode("utf-8")) < 700

    filtered = _ok(
        _run_context(
            wyrd_process,
            project_dir,
            "tickets",
            "--status",
            "all",
            "--label",
            "bug",
            "--text",
            "STARTUP",
        )
    )
    assert [item["id"] for item in filtered] == [1]

    tasks_result = _run_context(
        wyrd_process, project_dir, "tasks", "--ticket", "1"
    )
    tasks = _ok(tasks_result)
    assert [item["id"] for item in tasks] == ["1.1"]
    assert set(tasks[0]) == TASK_CONTEXT_KEYS
    assert len(tasks_result.stdout.encode("utf-8")) < 500
    all_tasks = _ok(
        _run_context(
            wyrd_process, project_dir, "tasks", "--ticket", "1", "--status", "all"
        )
    )
    assert [item["id"] for item in all_tasks] == ["1.1", "1.2"]
    assert _managed_bytes(project_dir) == before


def test_compact_context_helper_preserves_cli_errors_and_reports_missing_executable(
    wyrd_process, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    missing_project = _run_context(wyrd_process, outside, "tickets")
    assert missing_project.returncode == 1
    assert missing_project.stdout == ""
    assert assert_json_document(missing_project.stderr)["error"]["code"] == "project_not_found"

    invalid_status = _run_context(
        wyrd_process, outside, "tickets", "--status", "future"
    )
    assert invalid_status.returncode == 2
    assert invalid_status.stdout == ""
    assert assert_json_document(invalid_status.stderr)["error"]["code"] == "usage_error"

    missing_executable = subprocess.run(
        [
            sys.executable,
            str(CONTEXT_HELPER),
            "--wyrd",
            str(tmp_path / "does-not-exist"),
            "tickets",
        ],
        cwd=outside,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15,
        check=False,
    )
    assert missing_executable.returncode == 127
    assert missing_executable.stdout == ""
    error = assert_json_document(missing_executable.stderr)["error"]
    assert error["code"] == "wyrd_not_found"
    assert error["details"] == {"executable": str(tmp_path / "does-not-exist")}
