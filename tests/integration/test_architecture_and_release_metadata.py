from __future__ import annotations

import ast
import re
import tomllib
from importlib.metadata import distribution
from pathlib import Path

import wyrd_cli


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "wyrd_cli"

EXPECTED_CLASSIFIERS = {
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
}
EXPECTED_PROJECT_URLS = {
    "Homepage": "https://github.com/debonzi/wyrd",
    "Repository": "https://github.com/debonzi/wyrd",
    "Issues": "https://github.com/debonzi/wyrd/issues",
    "Changelog": "https://github.com/debonzi/wyrd/blob/main/CHANGELOG.md",
}
EXPECTED_RUNTIME_DEPENDENCIES = {
    "pydantic>=2.12,<3",
    "pyyaml>=6.0,<7",
    "rich>=14.0,<15",
    "typer>=0.21,<1",
}


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
    version = project["version"]
    assert version == wyrd_cli.__version__
    assert project["requires-python"] == ">=3.12"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["readme"] == "README.md"
    assert project["scripts"] == {"wyrd": "wyrd_cli.main:main"}
    assert set(project["keywords"]) == {
        "cli",
        "coding-agents",
        "issue-tracker",
        "project-management",
        "ticket-management",
    }
    assert EXPECTED_CLASSIFIERS <= set(project["classifiers"])
    assert not any(
        platform in classifier
        for classifier in project["classifiers"]
        for platform in ("MacOS", "Microsoft :: Windows")
    )
    assert EXPECTED_PROJECT_URLS.items() <= project["urls"].items()
    assert set(project["dependencies"]) == EXPECTED_RUNTIME_DEPENDENCIES
    dev_group = configuration["dependency-groups"]["dev"]
    assert "pytest>=9.0,<10" in dev_group
    assert not any(requirement.startswith("twine") for requirement in dev_group)
    assert configuration["dependency-groups"]["release"] == ["twine==6.2.0"]

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Permission is hereby granted, free of charge" in license_text

    installed = distribution("wyrd-cli")
    assert installed.version == version
    assert installed.metadata["Requires-Python"] == ">=3.12"
    assert installed.metadata["License-Expression"] == "MIT"
    assert installed.metadata.get_all("License-File") == ["LICENSE"]
    assert installed.metadata["Description-Content-Type"] == "text/markdown"
    assert set(installed.metadata.get_all("Requires-Dist") or []) == (
        EXPECTED_RUNTIME_DEPENDENCIES
    )
    assert EXPECTED_CLASSIFIERS <= set(
        installed.metadata.get_all("Classifier") or []
    )
    installed_urls = {
        label: url
        for label, url in (
            value.split(", ", 1)
            for value in installed.metadata.get_all("Project-URL") or []
        )
    }
    assert EXPECTED_PROJECT_URLS.items() <= installed_urls.items()
    scripts = {
        entry.name: entry.value
        for entry in installed.entry_points
        if entry.group == "console_scripts"
    }
    assert scripts["wyrd"] == "wyrd_cli.main:main"


def test_pypi_readme_documents_installation_platform_and_separate_skill() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("## Installation") < readme.index("## Getting started")
    version = wyrd_cli.__version__
    for command in (
        "uv tool install wyrd-cli",
        "pipx install wyrd-cli",
        "python -m pip install wyrd-cli",
        "pi --skill ./skills/wyrd",
        f"pi install git:github.com/debonzi/wyrd@v{version}",
    ):
        assert command in readme

    prose = " ".join(readme.split())
    assert f"Wyrd {version} supports Linux only" in prose
    assert "requires Python 3.12 or newer" in prose
    assert "Windows and macOS are not currently supported" in prose
    assert "provides the `wyrd` command" in prose
    assert (
        "plain `pip` installation should normally be performed inside a virtual environment"
        in prose
    )
    assert "Installing the skill does not install the Wyrd CLI" in prose
    assert "not bundled in the Python wheel or source distribution" in prose
    assert "matching Git repository tag" in prose

    link_targets = re.findall(r"\[[^]]+\]\(([^)]+)\)", readme)
    assert link_targets
    assert all(target.startswith("https://") for target in link_targets)
    assert "https://github.com/debonzi/wyrd/tree/main/skills/wyrd" in link_targets
    assert (
        "https://github.com/debonzi/wyrd/blob/main/skills/wyrd/SKILL.md"
        in link_targets
    )

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in changelog
    assert f"## {version} - Pending" in changelog
    assert "Initial alpha release for Linux" in changelog
