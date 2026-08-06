from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wyrd_cli.bootstrap import create_cli


def test_real_composition_root_smoke_only_uses_temporary_project(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    app = create_cli()

    initialized = runner.invoke(app, ["init", "--name", "Temporary", "--json"])
    assert initialized.exit_code == 0, initialized.stderr
    project = json.loads(initialized.stdout)
    assert project["root"] == str(tmp_path)

    created = runner.invoke(
        app,
        [
            "ticket",
            "create",
            "--title",
            "Composition smoke",
            "--body",
            "body",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.stderr
    assert json.loads(created.stdout)["id"] == 1

    viewed = runner.invoke(app, ["ticket", "view", "1", "--json"])
    assert viewed.exit_code == 0
    assert json.loads(viewed.stdout)["title"] == "Composition smoke"

    status = runner.invoke(app, ["status", "--json"])
    assert json.loads(status.stdout)["tickets"]["total"] == 1


def test_composition_import_does_not_discover_or_create_project(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_cli()
    assert not (tmp_path / ".wyrd").exists()
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert not (tmp_path / ".wyrd").exists()
