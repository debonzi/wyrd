from __future__ import annotations

import hashlib
from pathlib import Path

from tests.integration.conftest import assert_json_document


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


def _ok_json(result):
    assert result.returncode == 0, (result.args, result.stdout, result.stderr)
    assert result.stderr == ""
    return assert_json_document(result.stdout)


def _fingerprint(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root / ".wyrd").as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted((root / ".wyrd").rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_installed_console_script_summary_workflow_is_compact_compatible_and_read_only(
    wyrd_process, project_dir: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    assert _ok_json(
        wyrd_process.run(project_dir, "ticket", "list", "--summary", "--json")
    ) == []

    large_body = "private-marker-é\n" * 2_000
    _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "First",
            "--body",
            large_body,
            "--label",
            "bug",
            "--label",
            "p0",
            "--json",
        )
    )
    _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Second",
            "--json",
        )
    )
    _ok_json(
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
    for title in ("One", "Two"):
        _ok_json(
            wyrd_process.run(
                project_dir,
                "task",
                "create",
                "--ticket",
                "1",
                "--title",
                title,
                "--body",
                large_body,
                "--json",
            )
        )
    _ok_json(
        wyrd_process.run(
            project_dir,
            "task",
            "dependency",
            "add",
            "1.2",
            "--blocked-by",
            "1.1",
            "--json",
        )
    )

    before = _fingerprint(project_dir)
    full_tickets_result = wyrd_process.run(
        project_dir, "ticket", "list", "--status", "all", "--json"
    )
    full_tickets = _ok_json(full_tickets_result)
    summarized_tickets_result = wyrd_process.run(
        project_dir,
        "ticket",
        "list",
        "--status",
        "all",
        "--label",
        "bug",
        "--label",
        "p0",
        "--text",
        "PRIVATE-MARKER",
        "--summary",
        "--json",
    )
    summarized_tickets = _ok_json(summarized_tickets_result)
    assert [item["id"] for item in summarized_tickets] == [1]
    assert set(summarized_tickets[0]) == TICKET_SUMMARY_KEYS
    assert summarized_tickets[0]["active_blocked_by"] == [2]
    assert summarized_tickets[0]["tasks_summary"]["total"] == 2
    assert "body" not in summarized_tickets[0]
    assert "tasks" not in summarized_tickets[0]
    assert full_tickets[0]["body"] == large_body
    assert {"created_at", "updated_at", "blocked_by", "tasks"} <= set(
        full_tickets[0]
    )
    assert len(summarized_tickets_result.stdout) < len(full_tickets_result.stdout) // 10

    summarized_tasks = _ok_json(
        wyrd_process.run(
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
    )
    assert [item["id"] for item in summarized_tasks] == ["1.1", "1.2"]
    assert all(set(item) == TASK_SUMMARY_KEYS for item in summarized_tasks)
    assert summarized_tasks[0]["active_blocking"] == ["1.2"]
    assert summarized_tasks[1]["active_blocked_by"] == ["1.1"]
    assert _ok_json(
        wyrd_process.run(
            project_dir,
            "task",
            "list",
            "--ticket",
            "2",
            "--summary",
            "--json",
        )
    ) == []

    full_tasks = _ok_json(
        wyrd_process.run(project_dir, "task", "list", "--ticket", "1", "--json")
    )
    assert full_tasks[0]["body"] == large_body
    assert {"created_at", "updated_at", "blocked_by", "blocking"} <= set(full_tasks[0])

    human_tickets = wyrd_process.run(project_dir, "ticket", "list", "--status", "all")
    human_ticket_summaries = wyrd_process.run(
        project_dir, "ticket", "list", "--status", "all", "--summary"
    )
    assert human_ticket_summaries.returncode == human_tickets.returncode == 0
    assert human_ticket_summaries.stdout == human_tickets.stdout
    assert human_ticket_summaries.stderr == human_tickets.stderr == ""

    human_tasks = wyrd_process.run(project_dir, "task", "list", "--ticket", "1")
    human_task_summaries = wyrd_process.run(
        project_dir, "task", "list", "--ticket", "1", "--summary"
    )
    assert human_task_summaries.returncode == human_tasks.returncode == 0
    assert human_task_summaries.stdout == human_tasks.stdout
    assert human_task_summaries.stderr == human_tasks.stderr == ""
    assert _fingerprint(project_dir) == before
