from __future__ import annotations

import ast
import tomllib
from importlib.metadata import distribution
from pathlib import Path

import wyrd_cli


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "wyrd_cli"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _layer_imports(layer: str) -> set[str]:
    return {
        imported
        for path in (SOURCE / layer).rglob("*.py")
        for imported in _imports(path)
    }


def test_import_boundaries_keep_rules_storage_and_presentation_separate() -> None:
    core_imports = _layer_imports("domain") | _layer_imports("application")
    assert not {
        name
        for name in core_imports
        if name.split(".")[0] in {"fcntl", "pathlib", "rich", "typer", "yaml"}
        or name.startswith("wyrd_cli.infrastructure")
        or name.startswith("wyrd_cli.presentation")
    }

    presentation_imports = _layer_imports("presentation")
    assert not {
        name
        for name in presentation_imports
        if name.split(".")[0] in {"fcntl", "yaml"}
        or name.startswith("wyrd_cli.infrastructure")
    }
    assert ".wyrd" not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SOURCE / "presentation").rglob("*.py")
    )

    infrastructure_imports = _layer_imports("infrastructure")
    assert not {
        name
        for name in infrastructure_imports
        if name.split(".")[0] in {"rich", "typer"}
        or name.startswith("wyrd_cli.presentation")
    }

    main_imports = _imports(SOURCE / "main.py")
    assert {
        name for name in main_imports if name != "__future__"
    } == {"wyrd_cli.bootstrap"}


def test_release_metadata_license_dependencies_and_installed_entry_point() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = configuration["project"]
    assert configuration["build-system"]["build-backend"] == "uv_build"
    assert project["name"] == "wyrd-cli"
    assert project["version"] == wyrd_cli.__version__ == "0.1.0"
    assert project["requires-python"] == ">=3.12"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["readme"] == "README.md"
    assert project["scripts"] == {"wyrd": "wyrd_cli.main:main"}
    assert {item.split(">", 1)[0].split("<", 1)[0].casefold() for item in project["dependencies"]} == {
        "pydantic",
        "pyyaml",
        "rich",
        "typer",
    }
    assert len(configuration["dependency-groups"]["dev"]) == 1
    assert configuration["dependency-groups"]["dev"][0].startswith("pytest")

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Permission is hereby granted, free of charge" in license_text
    assert (ROOT / "README.md").is_file()

    installed = distribution("wyrd-cli")
    assert installed.version == "0.1.0"
    assert installed.metadata["License-Expression"] == "MIT"
    scripts = {entry.name: entry.value for entry in installed.entry_points if entry.group == "console_scripts"}
    assert scripts["wyrd"] == "wyrd_cli.main:main"
