from __future__ import annotations

import errno
import hashlib
import os
import pty
import select
import shutil
import signal
import time
from pathlib import Path

import pytest

from tests.integration.conftest import assert_json_document


def _ok_json(result):
    assert result.returncode == 0, (result.args, result.stdout, result.stderr)
    assert result.stderr == ""
    return assert_json_document(result.stdout)


def _error_json(result, code: str, *, exit_code: int = 1):
    assert result.returncode == exit_code, (result.args, result.stdout, result.stderr)
    assert result.stdout == ""
    value = assert_json_document(result.stderr)
    assert value["error"]["code"] == code
    assert isinstance(value["error"]["details"], dict)
    return value


def _canonical_fingerprint(root: Path) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for path in sorted((root / ".wyrd").rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(root / ".wyrd").as_posix()
            result[relative] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
    return result


def test_init_discovery_status_nested_partial_and_symlink_boundaries(
    wyrd_process, project_dir: Path, tmp_path: Path
) -> None:
    initialized = _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    assert initialized["name"] == project_dir.name
    assert initialized["root"] == str(project_dir)
    assert set(path.name for path in (project_dir / ".wyrd").iterdir()) == {
        "project.yaml",
        "lock",
        "tickets",
    }

    same = _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    assert same == initialized
    _error_json(
        wyrd_process.run(project_dir, "init", "--name", "Different", "--json"),
        "conflict",
    )

    descendant = project_dir / "one" / "two" / "three"
    descendant.mkdir(parents=True)
    status = _ok_json(wyrd_process.run(descendant, "status", "--json"))
    assert status["project"] == initialized
    assert status["tickets"] == {
        "blocked": 0,
        "completed": 0,
        "dismissed": 0,
        "open": 0,
        "total": 0,
    }
    assert status["tasks"]["total"] == status["tasks"]["open"] == 0

    outside = tmp_path / "outside"
    outside.mkdir()
    missing = _error_json(wyrd_process.run(outside, "status", "--json"), "project_not_found")
    assert "wyrd init" in missing["error"]["message"]

    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / ".wyrd").mkdir()
    _error_json(wyrd_process.run(partial, "init", "--json"), "invalid_project")
    assert list((partial / ".wyrd").iterdir()) == []

    inner = project_dir / "one"
    _error_json(
        wyrd_process.run(inner, "init", "--name", "Inner", "--json"),
        "project_already_exists",
    )
    shutil.copytree(project_dir / ".wyrd", inner / ".wyrd")
    _error_json(wyrd_process.run(descendant, "status", "--json"), "nested_project")

    safe = tmp_path / "symlink-project"
    safe.mkdir()
    _ok_json(wyrd_process.run(safe, "init", "--json"))
    tickets = safe / ".wyrd" / "tickets"
    moved = safe / "real-tickets"
    tickets.rename(moved)
    tickets.symlink_to(moved, target_is_directory=True)
    _error_json(wyrd_process.run(safe, "status", "--json"), "invalid_project")

    unicode_root = tmp_path / "unicode-name"
    unicode_root.mkdir()
    assert _ok_json(
        wyrd_process.run(unicode_root, "init", "--name", "é" * 100, "--json")
    )["name"] == "é" * 100
    too_long = tmp_path / "too-long-name"
    too_long.mkdir()
    _error_json(
        wyrd_process.run(too_long, "init", "--name", "é" * 101, "--json"),
        "validation_error",
    )
    assert not (too_long / ".wyrd").exists()


def test_ticket_workflow_body_sources_revisions_confirmation_and_granularity(
    wyrd_process, project_dir: Path, tmp_path: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--name", "Tickets", "--json"))
    body_file = tmp_path / "body.md"
    body_file.write_text("file body é\n\n", encoding="utf-8", newline="")

    first = _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            " First ",
            "--body",
            "literal\rbody",
            "--label",
            "p0",
            "--label",
            "bug",
            "--label",
            "bug",
            "--json",
        )
    )
    assert first["id"] == 1
    assert first["title"] == "First"
    assert first["body"] == "literal\nbody"
    assert first["labels"] == ["bug", "p0"]

    second = _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Second",
            "--body-file",
            str(body_file),
            "--json",
        )
    )
    third = _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Third",
            "--body-file",
            "-",
            "--json",
            input="stdin body é\n",
        )
    )
    assert second["body"] == "file body é\n\n"
    assert third["body"] == "stdin body é\n"
    assert [item["id"] for item in _ok_json(
        wyrd_process.run(project_dir, "ticket", "list", "--json")
    )] == [1, 2, 3]

    before_edit = _canonical_fingerprint(project_dir)
    edited = _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "edit",
            "1",
            "--title",
            "Changed",
            "--add-label",
            "testing",
            "--expected-revision",
            "1",
            "--json",
        )
    )
    assert edited["revision"] == 2
    after_edit = _canonical_fingerprint(project_dir)
    changed = {name for name in before_edit if before_edit[name] != after_edit[name]}
    assert changed == {"tickets/1/ticket.md"}

    stale = wyrd_process.run(
        project_dir,
        "ticket",
        "edit",
        "1",
        "--title",
        "Stale",
        "--expected-revision",
        "1",
        "--json",
    )
    _error_json(stale, "revision_conflict")
    assert _ok_json(wyrd_process.run(project_dir, "ticket", "view", "1", "--json"))[
        "title"
    ] == "Changed"

    required = wyrd_process.run(project_dir, "ticket", "complete", "1", "--json")
    _error_json(required, "confirmation_required")
    assert "Proceed" not in required.stderr
    completed = _ok_json(
        wyrd_process.run(project_dir, "ticket", "complete", "1", "--yes", "--json")
    )
    assert completed["status"] == "completed"
    assert completed["revision"] == 3
    assert _ok_json(
        wyrd_process.run(project_dir, "ticket", "complete", "1", "--json")
    )["revision"] == 3
    _error_json(
        wyrd_process.run(project_dir, "ticket", "dismiss", "1", "--json"),
        "conflict",
    )
    _error_json(
        wyrd_process.run(
            project_dir, "ticket", "edit", "1", "--body", "no", "--json"
        ),
        "resource_not_active",
    )


def test_task_hierarchy_lifecycle_inactivity_and_status_equations(
    wyrd_process, project_dir: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--name", "Tasks", "--json"))
    parent = _ok_json(
        wyrd_process.run(project_dir, "ticket", "create", "--title", "Parent", "--json")
    )
    parent_file = project_dir / ".wyrd/tickets/1/ticket.md"
    parent_before = (parent_file.read_bytes(), parent_file.stat().st_mtime_ns)

    one = _ok_json(
        wyrd_process.run(
            project_dir, "task", "create", "--ticket", "1", "--title", "One", "--json"
        )
    )
    two = _ok_json(
        wyrd_process.run(
            project_dir, "task", "create", "--ticket", "1", "--title", "Two", "--json"
        )
    )
    assert [one["id"], two["id"]] == ["1.1", "1.2"]
    assert all(isinstance(item["id"], str) for item in _ok_json(
        wyrd_process.run(project_dir, "task", "list", "--ticket", "1", "--json")
    ))
    assert (parent_file.read_bytes(), parent_file.stat().st_mtime_ns) == parent_before

    edited = _ok_json(
        wyrd_process.run(
            project_dir,
            "task",
            "edit",
            "1.1",
            "--title",
            "Edited",
            "--expected-revision",
            "1",
            "--json",
        )
    )
    assert edited["revision"] == 2
    assert _ok_json(wyrd_process.run(project_dir, "ticket", "view", "1", "--json"))[
        "revision"
    ] == parent["revision"]

    _error_json(
        wyrd_process.run(project_dir, "ticket", "complete", "1", "--yes", "--json"),
        "ticket_has_open_tasks",
    )
    _ok_json(wyrd_process.run(project_dir, "task", "complete", "1.1", "--yes", "--json"))
    _ok_json(wyrd_process.run(project_dir, "task", "dismiss", "1.2", "--yes", "--json"))
    _ok_json(wyrd_process.run(project_dir, "ticket", "complete", "1", "--yes", "--json"))

    _ok_json(
        wyrd_process.run(project_dir, "ticket", "create", "--title", "Dismissed parent", "--json")
    )
    _ok_json(
        wyrd_process.run(
            project_dir,
            "task",
            "create",
            "--ticket",
            "2",
            "--title",
            "Historical open",
            "--json",
        )
    )
    child_file = project_dir / ".wyrd/tickets/2/tasks/1.md"
    child_before = child_file.read_bytes()
    dismissed = _ok_json(
        wyrd_process.run(project_dir, "ticket", "dismiss", "2", "--yes", "--json")
    )
    assert dismissed["tasks_summary"]["inactive_open"] == 1
    inactive = _ok_json(wyrd_process.run(project_dir, "task", "view", "2.1", "--json"))
    assert inactive["status"] == "open"
    assert inactive["active"] is False
    assert child_file.read_bytes() == child_before
    _error_json(
        wyrd_process.run(project_dir, "task", "dismiss", "2.1", "--yes", "--json"),
        "resource_not_active",
    )
    assert [item["id"] for item in _ok_json(
        wyrd_process.run(
            project_dir, "task", "list", "--ticket", "2", "--status", "open", "--json"
        )
    )] == ["2.1"]

    status = _ok_json(wyrd_process.run(project_dir, "status", "--json"))
    assert status["tickets"]["total"] == sum(
        status["tickets"][name] for name in ("open", "completed", "dismissed")
    )
    assert status["tasks"]["total"] == sum(
        status["tasks"][name] for name in ("open", "completed", "dismissed")
    )
    assert status["tasks"]["open"] == (
        status["tasks"]["active_open"] + status["tasks"]["inactive_open"]
    )


def test_inline_labels_filters_text_and_aggregation(
    wyrd_process, project_dir: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    twenty = "a" * 20
    first = _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Cafe\u0301 startup",
            "--body",
            "literal **Markdown**",
            "--label",
            "bug",
            "--label",
            "p0",
            "--label",
            twenty,
            "--json",
        )
    )
    assert first["labels"] == sorted(["bug", "p0", twenty])
    _error_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Bad label",
            "--label",
            "a" * 21,
            "--json",
        ),
        "invalid_label",
    )
    _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Other",
            "--body",
            "A STRASSE detail",
            "--label",
            "bug",
            "--json",
        )
    )

    assert [item["id"] for item in _ok_json(
        wyrd_process.run(
            project_dir, "ticket", "list", "--label", "bug", "--label", "p0", "--json"
        )
    )] == [1]
    assert _ok_json(
        wyrd_process.run(project_dir, "ticket", "list", "--label", "unused", "--json")
    ) == []
    assert [item["id"] for item in _ok_json(
        wyrd_process.run(project_dir, "ticket", "list", "--text", "CAFÉ", "--json")
    )] == [1]
    assert [item["id"] for item in _ok_json(
        wyrd_process.run(project_dir, "ticket", "list", "--text", "straße", "--json")
    )] == [2]
    assert _ok_json(
        wyrd_process.run(project_dir, "ticket", "list", "--text", "cafe", "--json")
    ) == []
    _error_json(
        wyrd_process.run(project_dir, "ticket", "list", "--text", "   ", "--json"),
        "validation_error",
    )

    _ok_json(
        wyrd_process.run(
            project_dir,
            "task",
            "create",
            "--ticket",
            "2",
            "--title",
            "Labeled task",
            "--label",
            "task_label",
            "--json",
        )
    )
    _ok_json(wyrd_process.run(project_dir, "ticket", "dismiss", "2", "--yes", "--json"))
    labels = _ok_json(wyrd_process.run(project_dir, "label", "list", "--json"))
    assert [item["name"] for item in labels] == sorted(item["name"] for item in labels)
    usage = {item["name"]: item for item in labels}
    assert usage["bug"] == {
        "name": "bug",
        "task_count": 0,
        "ticket_count": 2,
        "total_count": 2,
    }
    assert usage["task_label"]["task_count"] == 1
    assert _ok_json(wyrd_process.run(project_dir, "status", "--json"))["labels"][
        "distinct"
    ] == len(labels)


def test_ticket_and_sibling_task_dependencies_direction_cycles_and_ownership(
    wyrd_process, project_dir: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    for number in range(1, 6):
        _ok_json(
            wyrd_process.run(
                project_dir, "ticket", "create", "--title", f"Ticket {number}", "--json"
            )
        )

    ticket_files = {
        number: project_dir / f".wyrd/tickets/{number}/ticket.md"
        for number in range(1, 6)
    }
    before = {number: path.read_bytes() for number, path in ticket_files.items()}
    blocked = _ok_json(
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
    assert blocked["blocked_by"] == [2]
    assert blocked["active_blocked_by"] == [2]
    blocker = _ok_json(wyrd_process.run(project_dir, "ticket", "view", "2", "--json"))
    assert blocker["blocking"] == [1]
    assert ticket_files[1].read_bytes() != before[1]
    assert all(ticket_files[number].read_bytes() == before[number] for number in range(2, 6))
    persisted = ticket_files[1].read_text(encoding="utf-8")
    assert "blocked_by:\n  - 2\n" in persisted
    assert "blocking:" not in persisted

    _ok_json(
        wyrd_process.run(
            project_dir, "ticket", "dependency", "add", "2", "--blocked-by", "3", "--json"
        )
    )
    _error_json(
        wyrd_process.run(
            project_dir, "ticket", "dependency", "add", "3", "--blocked-by", "1", "--json"
        ),
        "dependency_cycle",
    )
    _error_json(
        wyrd_process.run(
            project_dir, "ticket", "dependency", "add", "4", "--blocked-by", "4", "--json"
        ),
        "invalid_dependency_scope",
    )
    _error_json(
        wyrd_process.run(project_dir, "ticket", "complete", "1", "--yes", "--json"),
        "blocked_by_open_dependency",
    )
    _ok_json(wyrd_process.run(project_dir, "ticket", "dismiss", "2", "--yes", "--json"))
    ineffective = _ok_json(wyrd_process.run(project_dir, "ticket", "view", "1", "--json"))
    assert ineffective["blocked_by"] == [2]
    assert ineffective["active_blocked_by"] == []

    for ticket_id in (4, 5):
        for title in ("One", "Two") if ticket_id == 4 else ("Other",):
            _ok_json(
                wyrd_process.run(
                    project_dir,
                    "task",
                    "create",
                    "--ticket",
                    str(ticket_id),
                    "--title",
                    title,
                    "--json",
                )
            )
    task_relation = _ok_json(
        wyrd_process.run(
            project_dir,
            "task",
            "dependency",
            "add",
            "4.2",
            "--blocked-by",
            "4.1",
            "--json",
        )
    )
    assert task_relation["blocked_by"] == ["4.1"]
    assert all(isinstance(value, str) for value in task_relation["blocked_by"])
    _error_json(
        wyrd_process.run(
            project_dir,
            "task",
            "dependency",
            "add",
            "4.1",
            "--blocked-by",
            "4.2",
            "--json",
        ),
        "dependency_cycle",
    )
    _error_json(
        wyrd_process.run(
            project_dir,
            "task",
            "dependency",
            "add",
            "4.1",
            "--blocked-by",
            "5.1",
            "--json",
        ),
        "invalid_dependency_scope",
    )
    listed = _ok_json(
        wyrd_process.run(project_dir, "task", "dependency", "list", "4.1", "--json")
    )
    assert listed["blocking"] == ["4.2"]


def test_doctor_mixed_problems_is_stdout_result_read_only_and_normal_reads_are_strict(
    wyrd_process, project_dir: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    for number in range(1, 4):
        _ok_json(
            wyrd_process.run(
                project_dir, "ticket", "create", "--title", f"Ticket {number}", "--json"
            )
        )
    one = project_dir / ".wyrd/tickets/1/ticket.md"
    two = project_dir / ".wyrd/tickets/2/ticket.md"
    three = project_dir / ".wyrd/tickets/3/ticket.md"
    one.write_bytes(one.read_bytes().replace(b"blocked_by: []\n", b"blocked_by:\n  - 2\n"))
    two.write_bytes(two.read_bytes().replace(b"blocked_by: []\n", b"blocked_by:\n  - 1\n"))
    three.write_bytes(three.read_bytes().replace(b"revision: 1\n", b"revision: 1\nunknown: true\n"))
    unexpected = project_dir / ".wyrd/tickets/not-a-ticket"
    unexpected.mkdir()
    outside = project_dir / "outside"
    outside.write_text("outside", encoding="utf-8")
    (project_dir / ".wyrd/tickets/link").symlink_to(outside)

    before = _canonical_fingerprint(project_dir)
    doctor = wyrd_process.run(project_dir, "doctor", "--json")
    assert doctor.returncode == 1
    assert doctor.stderr == ""
    report = assert_json_document(doctor.stdout)
    assert report["healthy"] is False
    ordering = [(item["path"], item["code"]) for item in report["problems"]]
    assert ordering == sorted(ordering)
    codes = {item["code"] for item in report["problems"]}
    assert {"dependency_cycle", "unknown_field", "unexpected_path"} <= codes
    assert _canonical_fingerprint(project_dir) == before

    _error_json(wyrd_process.run(project_dir, "status", "--json"), "corrupt_data")
    assert outside.read_text(encoding="utf-8") == "outside"

    lock = project_dir / ".wyrd/lock"
    lock.unlink()
    ordinary = wyrd_process.run(project_dir, "doctor", "--json")
    _error_json(ordinary, "invalid_project")
    assert not lock.exists()


def test_json_success_error_usage_streams_unicode_and_no_ansi(
    wyrd_process, project_dir: Path
) -> None:
    initialized = wyrd_process.run(project_dir, "init", "--name", "Projeto é", "--json")
    project = _ok_json(initialized)
    assert "é" in initialized.stdout
    assert "\\u00e9" not in initialized.stdout
    assert "\x1b" not in initialized.stdout + initialized.stderr
    assert project["name"] == "Projeto é"

    missing = wyrd_process.run(project_dir, "ticket", "view", "99", "--json")
    _error_json(missing, "ticket_not_found")
    usage = wyrd_process.run(project_dir, "ticket", "create", "--json")
    _error_json(usage, "usage_error", exit_code=2)
    assert "Usage:" not in usage.stderr
    assert "\x1b" not in missing.stderr + usage.stderr

    human = wyrd_process.run(project_dir, "ticket", "list", "--no-color")
    assert human.returncode == 0
    assert "\x1b" not in human.stdout + human.stderr


def test_real_tty_confirmation_uses_prompt_and_commits_after_acceptance(
    wyrd_process, project_dir: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    _ok_json(
        wyrd_process.run(project_dir, "ticket", "create", "--title", "TTY target", "--json")
    )

    master, slave = pty.openpty()
    process = None
    output = bytearray()
    try:
        process = wyrd_process.popen(
            project_dir,
            "ticket",
            "dismiss",
            "1",
            stdin=slave,
            stdout=slave,
            stderr=slave,
            text=False,
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 10
        while b"Proceed?" not in output:
            remaining = deadline - time.monotonic()
            assert remaining > 0, output.decode("utf-8", errors="replace")
            readable, _, _ = select.select([master], [], [], remaining)
            assert readable, output.decode("utf-8", errors="replace")
            output.extend(os.read(master, 4096))
        os.write(master, b"y\n")
        while process.poll() is None:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    output.extend(os.read(master, 4096))
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
        assert process.wait(timeout=5) == 0
    finally:
        if slave >= 0:
            os.close(slave)
        os.close(master)
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=5)

    text = output.decode("utf-8")
    assert "Resource: 1" in text
    assert "Requested status: dismissed" in text
    assert "Proceed?" in text
    assert "\x1b" not in text
    assert _ok_json(wyrd_process.run(project_dir, "ticket", "view", "1", "--json"))[
        "status"
    ] == "dismissed"
