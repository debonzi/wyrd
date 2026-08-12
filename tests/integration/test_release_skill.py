from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".agents" / "skills" / "wyrd-release"
SKILL_FILE = SKILL / "SKILL.md"


def _frontmatter() -> tuple[dict[str, object], str]:
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    encoded, body = text[4:].split("\n---\n", 1)
    value = yaml.safe_load(encoded)
    assert isinstance(value, dict)
    return value, body


def test_release_skill_is_project_scoped_not_globally_distributed() -> None:
    distributed_skills = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "skills").rglob("SKILL.md")
    }

    assert distributed_skills == {"skills/wyrd/SKILL.md"}
    assert SKILL_FILE.is_file()
    assert not (ROOT / "skills" / "wyrd-release").exists()


def test_release_skill_is_direct_only_and_encodes_the_production_workflow() -> None:
    metadata, body = _frontmatter()

    assert set(metadata) == {
        "name",
        "description",
        "license",
        "compatibility",
        "disable-model-invocation",
    }
    assert metadata["name"] == SKILL.name == "wyrd-release"
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", str(metadata["name"]))
    assert 1 <= len(str(metadata["description"])) <= 1024
    assert metadata["license"] == "MIT"
    assert metadata["disable-model-invocation"] is True

    for required in (
        "/skill:wyrd-release",
        "git status --porcelain",
        "uv run --locked pytest",
        "scripts/release.py",
        "git diff --check",
        'git commit -m "Prepare release X.Y.Z"',
        "Create the PR to `main` using the available GitHub integration",
        "monitor its checks",
        "merge it using the available GitHub integration",
        'git tag -a vX.Y.Z <exact-sha> -m "Release X.Y.Z"',
        "Create the GitHub Release as a draft",
        "change the draft GitHub Release to published",
        "monitor it through the available GitHub integration",
        "If no suitable integration or client is available",
        "https://pypi.org/pypi/wyrd-cli/X.Y.Z/json",
        "Mandatory confirmations",
        "Final publication",
    ):
        assert required in body

    assert "gh-axi" not in SKILL_FILE.read_text(encoding="utf-8")
    assert "git tag -s" not in body
    assert "git add -A" in body  # It is explicitly forbidden.
    assert "skip-existing" in body  # It is explicitly forbidden.
    assert len(SKILL_FILE.read_text(encoding="utf-8").splitlines()) < 500
