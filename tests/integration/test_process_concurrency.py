from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.integration.conftest import assert_json_document


def _run_parallel(wyrd_process, cwd: Path, rows: list[tuple[str, ...]]):
    processes = [wyrd_process.popen(cwd, *row) for row in rows]
    results: list[tuple[int, str, str]] = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            results.append((process.returncode, stdout, stderr))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    return results


def _assert_healthy(wyrd_process, root: Path, *, tickets: int, tasks: int) -> None:
    status_result = wyrd_process.run(root, "status", "--json", timeout=30)
    assert status_result.returncode == 0, status_result.stderr
    status = assert_json_document(status_result.stdout)
    assert status["tickets"]["total"] == tickets
    assert status["tasks"]["total"] == tasks
    doctor = wyrd_process.run(root, "doctor", "--json", timeout=30)
    assert doctor.returncode == 0, doctor.stderr
    assert assert_json_document(doctor.stdout) == {"healthy": True, "problems": []}


def test_concurrent_cli_init_ticket_and_task_creation_publish_unique_complete_resources(
    wyrd_process, project_dir: Path
) -> None:
    init_count = 6
    initialized = _run_parallel(
        wyrd_process,
        project_dir,
        [("init", "--name", "Concurrent", "--json") for _ in range(init_count)],
    )
    assert all(code == 0 and stderr == "" for code, _, stderr in initialized), initialized
    assert {assert_json_document(stdout)["name"] for _, stdout, _ in initialized} == {
        "Concurrent"
    }
    assert set(path.name for path in (project_dir / ".wyrd").iterdir()) == {
        "project.yaml",
        "lock",
        "tickets",
    }
    assert not list(project_dir.glob(".wyrd-init-*.tmp"))

    ticket_count = 8
    created_tickets = _run_parallel(
        wyrd_process,
        project_dir,
        [
            ("ticket", "create", "--title", f"Ticket {number}", "--json")
            for number in range(ticket_count)
        ],
    )
    assert all(code == 0 and stderr == "" for code, _, stderr in created_tickets), created_tickets
    ticket_ids = sorted(assert_json_document(stdout)["id"] for _, stdout, _ in created_tickets)
    assert ticket_ids == list(range(1, ticket_count + 1))
    for ticket_id in ticket_ids:
        directory = project_dir / f".wyrd/tickets/{ticket_id}"
        assert {path.name for path in directory.iterdir()} == {"ticket.md", "tasks"}
        assert (directory / "ticket.md").is_file()
        assert (directory / "tasks").is_dir()

    task_count = 8
    created_tasks = _run_parallel(
        wyrd_process,
        project_dir,
        [
            (
                "task",
                "create",
                "--ticket",
                "1",
                "--title",
                f"Task {number}",
                "--json",
            )
            for number in range(task_count)
        ],
    )
    assert all(code == 0 and stderr == "" for code, _, stderr in created_tasks), created_tasks
    task_ids = sorted(
        (assert_json_document(stdout)["id"] for _, stdout, _ in created_tasks),
        key=lambda value: int(value.split(".")[1]),
    )
    assert task_ids == [f"1.{number}" for number in range(1, task_count + 1)]
    assert {
        path.name for path in (project_dir / ".wyrd/tickets/1/tasks").iterdir()
    } == {f"{number}.md" for number in range(1, task_count + 1)}
    _assert_healthy(
        wyrd_process,
        project_dir,
        tickets=ticket_count,
        tasks=task_count,
    )


def test_concurrent_cli_init_with_different_names_has_one_consistent_winner(
    wyrd_process, project_dir: Path
) -> None:
    rows = [
        ("init", "--name", name, "--json")
        for name in ("Alpha", "Beta")
        for _ in range(3)
    ]
    results = _run_parallel(wyrd_process, project_dir, rows)
    successes = [assert_json_document(stdout) for code, stdout, _ in results if code == 0]
    failures = [assert_json_document(stderr) for code, _, stderr in results if code == 1]
    assert len(successes) == 3, results
    assert len({item["name"] for item in successes}) == 1
    assert len(failures) == 3
    assert {item["error"]["code"] for item in failures} == {"conflict"}
    discovered = wyrd_process.run(project_dir, "status", "--json")
    assert discovered.returncode == 0
    assert assert_json_document(discovered.stdout)["project"]["name"] == successes[0]["name"]
    _assert_healthy(wyrd_process, project_dir, tickets=0, tasks=0)


def test_same_expected_revision_allows_exactly_one_cli_edit_commit(
    wyrd_process, project_dir: Path
) -> None:
    assert wyrd_process.run(project_dir, "init", "--json").returncode == 0
    assert wyrd_process.run(
        project_dir, "ticket", "create", "--title", "Editable", "--json"
    ).returncode == 0

    count = 8
    edits = _run_parallel(
        wyrd_process,
        project_dir,
        [
            (
                "ticket",
                "edit",
                "1",
                "--title",
                f"Winner {number}",
                "--expected-revision",
                "1",
                "--json",
            )
            for number in range(count)
        ],
    )
    successes = [assert_json_document(stdout) for code, stdout, _ in edits if code == 0]
    failures = [assert_json_document(stderr) for code, _, stderr in edits if code == 1]
    assert len(successes) == 1, edits
    assert successes[0]["revision"] == 2
    assert len(failures) == count - 1
    assert {failure["error"]["code"] for failure in failures} == {"revision_conflict"}

    view = wyrd_process.run(project_dir, "ticket", "view", "1", "--json")
    assert view.returncode == 0
    current = assert_json_document(view.stdout)
    assert current["revision"] == 2
    assert current["title"] == successes[0]["title"]
    _assert_healthy(wyrd_process, project_dir, tickets=1, tasks=0)


def test_cli_lock_timeout_and_process_termination_release_kernel_lock(
    wyrd_process, project_dir: Path
) -> None:
    assert wyrd_process.run(project_dir, "init", "--json").returncode == 0
    lock_path = project_dir / ".wyrd/lock"
    script = (
        "import fcntl, os, signal, sys, time; "
        "fd=os.open(sys.argv[1], os.O_RDWR); "
        "fcntl.flock(fd, fcntl.LOCK_EX); "
        "print('READY', flush=True); "
        "signal.pause()"
    )
    holder = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(lock_path)],
        cwd=project_dir,
        env=wyrd_process.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "READY"
        timed_out = wyrd_process.run(
            project_dir, "status", "--lock-timeout", "0.05", "--json"
        )
        assert timed_out.returncode == 1
        assert timed_out.stdout == ""
        assert assert_json_document(timed_out.stderr)["error"]["code"] == "lock_timeout"
    finally:
        if holder.poll() is None:
            holder.terminate()
        holder.wait(timeout=5)

    status = wyrd_process.run(project_dir, "status", "--lock-timeout", "0", "--json")
    assert status.returncode == 0, status.stderr
    assert assert_json_document(status.stdout)["tickets"]["total"] == 0
    _assert_healthy(wyrd_process, project_dir, tickets=0, tasks=0)
