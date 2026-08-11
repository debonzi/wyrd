from __future__ import annotations

import hashlib
from pathlib import Path

from tests.integration.conftest import assert_json_document


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


def test_installed_console_script_tree_is_compact_filtered_and_read_only(
    wyrd_process, project_dir: Path
) -> None:
    _ok_json(wyrd_process.run(project_dir, "init", "--json"))
    large_body = "private-tree-marker-é\n" * 2_000
    _ok_json(
        wyrd_process.run(
            project_dir,
            "ticket",
            "create",
            "--title",
            "Startup work",
            "--body",
            large_body,
            "--label",
            "bug",
            "--json",
        )
    )
    _ok_json(
        wyrd_process.run(
            project_dir, "ticket", "create", "--title", "Finished", "--json"
        )
    )
    _ok_json(
        wyrd_process.run(
            project_dir, "ticket", "complete", "2", "--yes", "--json"
        )
    )
    for title in ("Done", "Pending"):
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
            project_dir, "task", "complete", "1.1", "--yes", "--json"
        )
    )

    before = _fingerprint(project_dir)
    default_result = wyrd_process.run(project_dir, "tree", "--json")
    default = _ok_json(default_result)
    assert [branch["ticket"]["id"] for branch in default] == [1]
    assert [task["id"] for task in default[0]["tasks"]] == ["1.1", "1.2"]
    assert "private-tree-marker-é" not in default_result.stdout
    assert "body" not in default[0]["ticket"]
    assert all("body" not in task for task in default[0]["tasks"])

    open_result = wyrd_process.run(
        project_dir,
        "tree",
        "--label",
        "bug",
        "--text",
        "STARTUP",
        "--task-status",
        "open",
        "--json",
    )
    opened = _ok_json(open_result)
    assert [task["id"] for task in opened[0]["tasks"]] == ["1.2"]

    human = wyrd_process.run(project_dir, "tree", "--task-status", "open")
    assert human.returncode == 0
    assert "1 [open] Startup work [labels: bug]" in human.stdout
    assert "1.2 [open] Pending" in human.stdout
    assert _fingerprint(project_dir) == before
