from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

import pytest

from scripts import release


ROOT = Path(__file__).resolve().parents[2]


def _copy_release_identity_files(destination: Path) -> None:
    for relative in (
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "CHANGELOG.md",
        "src/wyrd_cli/__init__.py",
        "skills/wyrd/SKILL.md",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)


def test_repository_release_identity_and_optional_tag_are_consistent() -> None:
    identity = release.validate_repository(ROOT)

    assert identity.name == "wyrd-cli"
    assert identity.normalized_name == "wyrd-cli"
    assert identity.wheel_filename.endswith("-py3-none-any.whl")
    release.validate_tag(f"v{identity.version}", identity.version)
    release.validate_tag(f"refs/tags/v{identity.version}", identity.version)
    release.validate_tag(None, identity.version)


@pytest.mark.parametrize(
    "tag",
    [
        "refs/heads/main",
        "refs/tags/not-a-version",
        "9.8.7",
        "v9.8.7",
    ],
)
def test_release_tag_rejects_malformed_or_mismatched_refs(tag: str) -> None:
    identity = release.validate_repository(ROOT)

    with pytest.raises(release.ReleaseCheckError, match="does not exactly match"):
        release.validate_tag(tag, identity.version)


def test_repository_check_rejects_source_version_drift_without_changing_checkout(
    tmp_path: Path,
) -> None:
    _copy_release_identity_files(tmp_path)
    source_version = tmp_path / "src" / "wyrd_cli" / "__init__.py"
    source_version.write_text(
        source_version.read_text(encoding="utf-8").replace(
            release.validate_repository(ROOT).version, "9.8.7"
        ),
        encoding="utf-8",
    )

    with pytest.raises(release.ReleaseCheckError, match="__version__"):
        release.validate_repository(tmp_path)


def test_repository_check_rejects_skill_version_drift_without_changing_checkout(
    tmp_path: Path,
) -> None:
    _copy_release_identity_files(tmp_path)
    skill = tmp_path / "skills" / "wyrd" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8").replace(
            f'version: "{release.validate_repository(ROOT).version}"',
            'version: "9.8.7"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(release.ReleaseCheckError, match="Skill metadata.version"):
        release.validate_repository(tmp_path)


@pytest.mark.parametrize("required_exclusion", ["/.agents", "/skills"])
def test_repository_check_requires_skill_scope_source_exclusions(
    tmp_path: Path, required_exclusion: str
) -> None:
    _copy_release_identity_files(tmp_path)
    configuration = tmp_path / "pyproject.toml"
    configuration.write_text(
        configuration.read_text(encoding="utf-8").replace(
            f'    "{required_exclusion}",\n', "", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        release.ReleaseCheckError,
        match=f"explicitly exclude {re.escape(required_exclusion)}",
    ):
        release.validate_repository(tmp_path)


@pytest.mark.parametrize(
    "member",
    [
        "package/.agents/AGENTS.md",
        "package/.drafts/release.md",
        "package/.git/config",
        "package/.venv/pyvenv.cfg",
        "package/dist/old.whl",
        "package/src/wyrd_cli/__pycache__/module.pyc",
        "package/.pytest_cache/CACHEDIR.TAG",
        "package/skills/wyrd/SKILL.md",
        "package/credentials.json",
        "/absolute/package.py",
        "package/../escape.py",
    ],
)
def test_archive_member_gate_rejects_forbidden_content(member: str) -> None:
    with pytest.raises(release.ReleaseCheckError):
        release.validate_member_names([member])


def test_artifact_inventory_rejects_unexpected_extra_file(tmp_path: Path) -> None:
    identity = release.validate_repository(ROOT)
    (tmp_path / identity.wheel_filename).touch()
    (tmp_path / identity.sdist_filename).touch()
    (tmp_path / "stale-upload.whl").touch()

    with pytest.raises(release.ReleaseCheckError, match="exactly one expected"):
        release.expected_artifacts(tmp_path, identity)


def test_checksum_manifest_records_only_fresh_distribution_files(
    tmp_path: Path,
) -> None:
    identity = release.validate_repository(ROOT)
    wheel = tmp_path / identity.wheel_filename
    sdist = tmp_path / identity.sdist_filename
    wheel.write_bytes(b"fresh wheel")
    sdist.write_bytes(b"fresh sdist")
    artifacts = release.expected_artifacts(tmp_path, identity)

    manifest = release.write_checksums(artifacts, tmp_path)

    assert manifest == tmp_path.with_name(f"{tmp_path.name}.sha256")
    lines = manifest.read_text(encoding="ascii").splitlines()
    assert len(lines) == 2
    assert {line.split("  ", 1)[1] for line in lines} == {
        identity.wheel_filename,
        identity.sdist_filename,
    }
    assert sorted(item.name for item in tmp_path.iterdir()) == sorted(
        [identity.wheel_filename, identity.sdist_filename]
    )


def test_artifact_directory_must_start_empty_unless_explicitly_cleared(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "candidate"
    artifact_dir.mkdir()
    (artifact_dir / "stale.tar.gz").touch()

    with pytest.raises(release.ReleaseCheckError, match="must start empty"):
        release.prepare_artifact_directory(artifact_dir, clear=False, root=ROOT)

    prepared = release.prepare_artifact_directory(artifact_dir, clear=True, root=ROOT)
    assert prepared == artifact_dir.resolve()
    assert list(prepared.iterdir()) == []


def test_artifact_directory_refuses_tracked_repository_locations() -> None:
    with pytest.raises(release.ReleaseCheckError, match="unsafe artifact directory"):
        release.prepare_artifact_directory(
            ROOT / "docs" / "release-output", clear=True, root=ROOT
        )


def test_ci_matrix_uv_pin_and_documented_preflight_interface() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required_uv = configuration["tool"]["uv"]["required-version"].removeprefix("==")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'python-version: ["3.12", "3.13", "3.14"]' in ci
    assert f'version: "{required_uv}"' in ci
    assert "permissions:\n  contents: read" in ci

    documentation = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
    command = (
        "uv run --locked --group release python scripts/release.py "
        "--artifact-dir dist/release --clear"
    )
    assert command in documentation.replace("\\\n", "")
    for output in ("artifact_dir", "wheel", "sdist", "checksum_manifest", "version"):
        assert f"`{output}`" in documentation
