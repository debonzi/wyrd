#!/usr/bin/env python3
"""Build, validate, and smoke-test Wyrd release artifacts.

This module intentionally uses only the Python standard library. The canonical
entry point runs it in the locked ``release`` dependency group, where Twine is
available for strict long-description validation.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ENTRY_POINT = "wyrd_cli.main:main"

FORBIDDEN_PATH_PARTS = {
    ".agents",
    ".drafts",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "dist",
    "venv",
}
FORBIDDEN_SECRET_NAMES = {
    ".env",
    ".netrc",
    ".pypirc",
    ".secrets",
    "credentials",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "secrets",
    "secrets.json",
}
FORBIDDEN_SECRET_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
FORBIDDEN_CONTENT = (
    b"BEGIN OPENSSH PRIVATE KEY",
    b"BEGIN PRIVATE KEY",
    b"AWS_SECRET_ACCESS_KEY=",
    b"PYPI_API_TOKEN=",
    b"file:///",
)


class ReleaseCheckError(RuntimeError):
    """An actionable release-gate failure."""


@dataclass(frozen=True)
class ReleaseIdentity:
    name: str
    version: str
    requires_python: str
    dependencies: tuple[str, ...]

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)

    @property
    def filename_name(self) -> str:
        return self.normalized_name.replace("-", "_")

    @property
    def filename_version(self) -> str:
        return self.version.replace("-", "_")

    @property
    def wheel_filename(self) -> str:
        return (
            f"{self.filename_name}-{self.filename_version}-py3-none-any.whl"
        )

    @property
    def sdist_filename(self) -> str:
        return f"{self.filename_name}-{self.filename_version}.tar.gz"

    @property
    def archive_root(self) -> str:
        return f"{self.filename_name}-{self.filename_version}"

    @property
    def dist_info(self) -> str:
        return f"{self.filename_name}-{self.filename_version}.dist-info"


@dataclass(frozen=True)
class ArtifactPaths:
    wheel: Path
    sdist: Path


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


def normalize_name(value: str) -> str:
    """Apply the PyPA project-name normalization rule."""
    return re.sub(r"[-_.]+", "-", value).lower()


def _fail(message: str) -> None:
    raise ReleaseCheckError(message)


def _source_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    versions = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            versions.append(value.value)
    if len(versions) != 1:
        _fail(f"expected exactly one literal __version__ assignment in {path}")
    return versions[0]


def validate_tag(tag: str | None, version: str) -> None:
    """Validate an explicitly supplied production tag or GitHub tag ref."""
    if not tag:
        return
    expected = {f"v{version}", f"refs/tags/v{version}"}
    if tag not in expected:
        _fail(
            f"release tag/ref {tag!r} does not exactly match v{version}; "
            f"expected one of {sorted(expected)}"
        )


def _skill_values(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        _fail(f"Agent Skill frontmatter is missing or malformed in {path}")
    frontmatter = text.split("---\n", 2)[1]
    version_match = re.search(
        r'^metadata:\s*\n(?:^[ \t]+.*\n)*?^[ \t]+version:\s*["\']?([^"\'\s]+)["\']?\s*$',
        frontmatter,
        flags=re.MULTILINE,
    )
    compatibility_match = re.search(
        r"^compatibility:\s*(.+?)\s*$", frontmatter, flags=re.MULTILINE
    )
    if not version_match or not compatibility_match:
        _fail(f"Agent Skill version/compatibility metadata is missing in {path}")
    return version_match.group(1), compatibility_match.group(1)


def validate_repository(
    root: Path = ROOT, *, tag: str | None = None, check_uv: bool = False
) -> ReleaseIdentity:
    """Validate all repository-level release identity sources."""
    root = root.resolve()
    configuration = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = configuration["project"]
    identity = ReleaseIdentity(
        name=str(project["name"]),
        version=str(project["version"]),
        requires_python=str(project["requires-python"]),
        dependencies=tuple(str(item) for item in project.get("dependencies", [])),
    )

    if identity.normalized_name != "wyrd-cli":
        _fail(f"unexpected normalized distribution name: {identity.normalized_name!r}")
    if _source_version(root / "src" / "wyrd_cli" / "__init__.py") != identity.version:
        _fail("pyproject.toml version and wyrd_cli.__version__ do not match")

    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    root_packages = [
        package
        for package in lock.get("package", [])
        if normalize_name(str(package.get("name", ""))) == identity.normalized_name
        and package.get("source") == {"editable": "."}
    ]
    if len(root_packages) != 1:
        _fail("uv.lock must contain exactly one editable root wyrd-cli package")
    if str(root_packages[0].get("version")) != identity.version:
        _fail("uv.lock root package version does not match pyproject.toml")

    skill_version, compatibility = _skill_values(root / "skills" / "wyrd" / "SKILL.md")
    if skill_version != identity.version:
        _fail("Agent Skill metadata.version does not match the package version")
    release_parts = identity.version.split(".")
    if len(release_parts) < 2 or not all(part.isdigit() for part in release_parts[:2]):
        _fail(f"cannot derive Agent Skill compatibility family from {identity.version!r}")
    compatibility_family = f"wyrd-cli {release_parts[0]}.{release_parts[1]}.x"
    if compatibility_family not in compatibility:
        _fail(
            "Agent Skill compatibility does not match the package major/minor family "
            f"({compatibility_family})"
        )

    readme = (root / "README.md").read_text(encoding="utf-8")
    for expected in (
        f"Wyrd {identity.version} supports Linux only",
        f"git:github.com/debonzi/wyrd@v{identity.version}",
        "not bundled in the Python wheel or source distribution",
    ):
        if expected not in readme:
            _fail(f"README release statement is missing: {expected!r}")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {identity.version} - " not in changelog:
        _fail("CHANGELOG has no section for the package version")

    build_configuration = configuration.get("tool", {}).get("uv", {})
    required_uv = str(build_configuration.get("required-version", ""))
    if not re.fullmatch(r"==\d+\.\d+\.\d+", required_uv):
        _fail("[tool.uv].required-version must be an exact version pin")
    source_excludes = configuration.get("tool", {}).get("uv", {}).get(
        "build-backend", {}
    ).get("source-exclude", [])
    for required_exclusion in ("/.agents", "/skills"):
        if required_exclusion not in source_excludes:
            _fail(
                "[tool.uv.build-backend].source-exclude must explicitly exclude "
                f"{required_exclusion}"
            )

    release_group = configuration.get("dependency-groups", {}).get("release", [])
    if release_group != ["twine==6.2.0"]:
        _fail("the release dependency group must contain only the exact Twine pin")

    if check_uv:
        uv = shutil.which("uv")
        if not uv:
            _fail("uv is not installed or not on PATH")
        result = _run([uv, "--version"], cwd=root)
        actual = result.stdout.strip().split()
        if len(actual) < 2 or actual[1] != required_uv.removeprefix("=="):
            _fail(
                f"uv version does not satisfy the project pin {required_uv}: "
                f"got {result.stdout.strip()!r}"
            )

    validate_tag(tag, identity.version)
    return identity


def prepare_artifact_directory(path: Path, *, clear: bool, root: Path = ROOT) -> Path:
    """Create a known-empty artifact directory without touching repository dist/."""
    path = path.expanduser().resolve()
    root = root.resolve()
    repository_dist = root / "dist"
    if (
        path == root
        or path in root.parents
        or path == repository_dist
        or (path.is_relative_to(root) and not path.is_relative_to(repository_dist))
    ):
        _fail(f"refusing unsafe artifact directory: {path}")
    manifest = checksum_path(path)
    if clear:
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                _fail(f"cannot clear non-directory artifact path: {path}")
            shutil.rmtree(path)
        if manifest.exists():
            if manifest.is_symlink() or not manifest.is_file():
                _fail(f"cannot clear non-file checksum path: {manifest}")
            manifest.unlink()
    path.mkdir(parents=True, exist_ok=True)
    entries = list(path.iterdir())
    if entries:
        _fail(
            f"artifact directory must start empty: {path} contains "
            + ", ".join(sorted(item.name for item in entries))
        )
    if manifest.exists():
        _fail(f"checksum output already exists; use --clear or remove it: {manifest}")
    return path


def checksum_path(artifact_dir: Path) -> Path:
    return artifact_dir.with_name(f"{artifact_dir.name}.sha256")


def expected_artifacts(artifact_dir: Path, identity: ReleaseIdentity) -> ArtifactPaths:
    entries = sorted(item.name for item in artifact_dir.iterdir())
    expected = sorted([identity.sdist_filename, identity.wheel_filename])
    if entries != expected:
        _fail(
            "release build must contain exactly one expected wheel and sdist; "
            f"expected {expected}, found {entries}"
        )
    wheel = artifact_dir / identity.wheel_filename
    sdist = artifact_dir / identity.sdist_filename
    if not wheel.is_file() or wheel.is_symlink() or not sdist.is_file() or sdist.is_symlink():
        _fail("release artifacts must be regular, non-symlink files")
    return ArtifactPaths(wheel=wheel, sdist=sdist)


def _run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    echo_output: bool = False,
) -> CommandResult:
    rendered = [os.fspath(item) for item in command]
    print(f"+ (cd {cwd} && {' '.join(rendered)})", flush=True)
    try:
        completed = subprocess.run(
            rendered,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        _fail(f"could not execute {rendered[0]!r}: {error}")
    if completed.returncode != 0:
        diagnostic = [
            f"command failed with exit code {completed.returncode}: {' '.join(rendered)}"
        ]
        if completed.stdout:
            diagnostic.append(f"stdout:\n{completed.stdout.rstrip()}")
        if completed.stderr:
            diagnostic.append(f"stderr:\n{completed.stderr.rstrip()}")
        _fail("\n".join(diagnostic))
    if echo_output:
        if completed.stdout:
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
        if completed.stderr:
            print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    return CommandResult(completed.stdout, completed.stderr)


def _archive_files(path: Path) -> tuple[list[str], dict[str, bytes]]:
    names: list[str] = []
    files: dict[str, bytes] = {}
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                names.append(info.filename)
                if not info.is_dir():
                    if info.filename in files:
                        _fail(f"duplicate wheel member: {info.filename}")
                    files[info.filename] = archive.read(info)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive.getmembers():
                names.append(member.name)
                if member.issym() or member.islnk():
                    _fail(f"archive links are not allowed: {member.name}")
                if member.isfile():
                    if member.name in files:
                        _fail(f"duplicate sdist member: {member.name}")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        _fail(f"could not read sdist member: {member.name}")
                    files[member.name] = extracted.read()
    else:
        _fail(f"unsupported artifact format: {path}")
    validate_member_names(names)
    _validate_archive_contents(files)
    return names, files


def validate_member_names(names: Iterable[str]) -> None:
    """Reject unsafe, generated, local-environment, and secret archive paths."""
    for name in names:
        if "\\" in name:
            _fail(f"archive member uses a backslash path separator: {name}")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            _fail(f"archive member has an unsafe path: {name}")
        lowered = tuple(part.lower() for part in path.parts)
        forbidden = FORBIDDEN_PATH_PARTS.intersection(lowered)
        if forbidden:
            _fail(f"forbidden packaged path component {sorted(forbidden)[0]!r}: {name}")
        if "skills" in lowered:
            _fail(f"the separately distributed Agent Skill must not be packaged: {name}")
        basename = lowered[-1] if lowered else ""
        if basename.endswith((".pyc", ".pyo")):
            _fail(f"Python bytecode must not be packaged: {name}")
        if basename in FORBIDDEN_SECRET_NAMES or any(
            basename.endswith(suffix) for suffix in FORBIDDEN_SECRET_SUFFIXES
        ):
            _fail(f"potential secret file must not be packaged: {name}")
        if basename == "pyvenv.cfg" or "site-packages" in lowered:
            _fail(f"local virtual environment content must not be packaged: {name}")


def _validate_archive_contents(files: Mapping[str, bytes]) -> None:
    local_markers = (
        os.fsencode(str(ROOT.resolve())),
        b"/home/",
        b"/Users/",
    )
    windows_home = re.compile(rb"[A-Za-z]:\\Users\\")
    for name, content in files.items():
        for marker in (*FORBIDDEN_CONTENT, *local_markers):
            if marker and marker in content:
                _fail(f"forbidden secret or absolute local path marker in {name}: {marker!r}")
        if windows_home.search(content):
            _fail(f"absolute Windows user path found in packaged file: {name}")


def _metadata(files: Mapping[str, bytes], path: str) -> Message:
    try:
        raw = files[path]
    except KeyError:
        _fail(f"required package metadata is missing: {path}")
    return BytesParser(policy=policy.default).parsebytes(raw)


def _normalized_requirement(requirement: str) -> tuple[str, str]:
    match = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", requirement)
    if not match:
        _fail(f"cannot parse runtime requirement: {requirement!r}")
    return normalize_name(match.group(1)), re.sub(r"\s+", "", match.group(2))


def _validate_metadata(message: Message, identity: ReleaseIdentity) -> None:
    name = str(message.get("Name", ""))
    if normalize_name(name) != identity.normalized_name:
        _fail(f"artifact metadata name does not match pyproject.toml: {name!r}")
    if str(message.get("Version", "")) != identity.version:
        _fail("artifact metadata version does not match pyproject.toml")
    if str(message.get("Description-Content-Type", "")).split(";", 1)[0] != "text/markdown":
        _fail("artifact long-description metadata must be Markdown")
    payload = message.get_payload()
    if not isinstance(payload, str) or not payload.strip() or "# Wyrd" not in payload:
        _fail("artifact long description is missing the README Markdown body")
    if message.get("License-Expression") != "MIT":
        _fail("artifact metadata must contain the MIT License-Expression")
    license_files = [str(item) for item in message.get_all("License-File", [])]
    if "LICENSE" not in license_files:
        _fail("artifact metadata must declare LICENSE")
    if message.get("Requires-Python") != identity.requires_python:
        _fail("artifact Requires-Python does not match pyproject.toml")
    artifact_requirements = {
        _normalized_requirement(str(item))
        for item in message.get_all("Requires-Dist", [])
    }
    project_requirements = {
        _normalized_requirement(item) for item in identity.dependencies
    }
    if artifact_requirements != project_requirements:
        _fail(
            "artifact runtime dependencies do not match pyproject.toml: "
            f"expected {sorted(project_requirements)}, got {sorted(artifact_requirements)}"
        )


def inspect_wheel(path: Path, identity: ReleaseIdentity) -> None:
    if path.name != identity.wheel_filename:
        _fail(f"unexpected wheel filename: {path.name}")
    names, files = _archive_files(path)
    dist_info = identity.dist_info
    required = {
        "wyrd_cli/__init__.py",
        "wyrd_cli/main.py",
        f"{dist_info}/METADATA",
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/licenses/LICENSE",
    }
    missing = required.difference(files)
    if missing:
        _fail(f"wheel is missing required files: {sorted(missing)}")
    if not all(
        name.startswith("wyrd_cli/") or name.startswith(f"{dist_info}/")
        for name in names
    ):
        _fail("wheel contains files outside wyrd_cli and its exact dist-info directory")
    _validate_metadata(_metadata(files, f"{dist_info}/METADATA"), identity)
    if not files[f"{dist_info}/licenses/LICENSE"].startswith(b"MIT License\n"):
        _fail("wheel LICENSE does not contain the MIT license text")

    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(files[f"{dist_info}/entry_points.txt"].decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as error:
        _fail(f"wheel entry_points.txt is invalid: {error}")
    scripts = dict(parser["console_scripts"]) if parser.has_section("console_scripts") else {}
    if scripts != {"wyrd": EXPECTED_ENTRY_POINT}:
        _fail(f"unexpected console entry points: {scripts}")

    wheel_metadata = files[f"{dist_info}/WHEEL"].decode("utf-8", errors="strict")
    if "Root-Is-Purelib: true" not in wheel_metadata or "Tag: py3-none-any" not in wheel_metadata:
        _fail("release wheel must be a py3-none-any pure-Python wheel")


def inspect_sdist(path: Path, identity: ReleaseIdentity) -> None:
    if path.name != identity.sdist_filename:
        _fail(f"unexpected sdist filename: {path.name}")
    names, files = _archive_files(path)
    root = identity.archive_root
    if not all(name == root or name.startswith(f"{root}/") for name in names):
        _fail(f"sdist members must share the exact root directory {root!r}")
    required = {
        f"{root}/PKG-INFO",
        f"{root}/pyproject.toml",
        f"{root}/README.md",
        f"{root}/LICENSE",
        f"{root}/src/wyrd_cli/__init__.py",
        f"{root}/src/wyrd_cli/main.py",
    }
    missing = required.difference(files)
    if missing:
        _fail(f"sdist is missing required files: {sorted(missing)}")
    _validate_metadata(_metadata(files, f"{root}/PKG-INFO"), identity)
    if not files[f"{root}/LICENSE"].startswith(b"MIT License\n"):
        _fail("sdist LICENSE does not contain the MIT license text")
    try:
        project = tomllib.loads(files[f"{root}/pyproject.toml"].decode("utf-8"))["project"]
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError) as error:
        _fail(f"sdist pyproject.toml is invalid: {error}")
    if project.get("scripts") != {"wyrd": EXPECTED_ENTRY_POINT}:
        _fail("sdist pyproject.toml has an unexpected console entry point")


def _sanitized_environment(home: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in (
        "CLICOLOR_FORCE",
        "FORCE_COLOR",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "CI": "1",
            "CLICOLOR": "0",
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
            "PYTHONNOUSERSITE": "1",
            "TERM": "dumb",
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
        }
    )
    return environment


def _json_result(result: CommandResult, description: str) -> object:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        _fail(f"{description} did not return valid JSON: {error}\n{result.stdout}")


def _installed_probe(
    python: Path,
    *,
    cwd: Path,
    env: Mapping[str, str],
    identity: ReleaseIdentity,
    checkout: Path,
) -> None:
    code = """
import importlib.metadata as metadata
import json
from pathlib import Path
import wyrd_cli

expected_version, expected_entry, checkout = ARGS
module_path = Path(wyrd_cli.__file__).resolve()
distribution = metadata.distribution("wyrd-cli")
scripts = {
    item.name: item.value
    for item in distribution.entry_points
    if item.group == "console_scripts"
}
assert wyrd_cli.__version__ == expected_version
assert distribution.version == expected_version
assert scripts == {"wyrd": expected_entry}
assert not module_path.is_relative_to(Path(checkout).resolve())
print(json.dumps({"module": str(module_path), "version": distribution.version}))
""".replace("ARGS", repr((identity.version, EXPECTED_ENTRY_POINT, str(checkout))))
    result = _run([python, "-I", "-c", code], cwd=cwd, env=env)
    payload = _json_result(result, "installed metadata probe")
    if not isinstance(payload, dict) or payload.get("version") != identity.version:
        _fail(f"installed metadata probe returned an unexpected result: {payload!r}")


def _exercise_installed_cli(
    environment_dir: Path,
    *,
    workspace: Path,
    env: Mapping[str, str],
    identity: ReleaseIdentity,
) -> None:
    executable = environment_dir / "bin" / "wyrd"
    python = environment_dir / "bin" / "python"
    if not executable.is_file() or not python.is_file():
        _fail(f"clean environment did not install the wyrd command: {environment_dir}")

    _installed_probe(
        python, cwd=workspace, env=env, identity=identity, checkout=ROOT
    )
    version = _run([executable, "--version"], cwd=workspace, env=env)
    if version.stdout.strip() != identity.version:
        _fail(
            f"installed wyrd --version returned {version.stdout.strip()!r}, "
            f"expected {identity.version!r}"
        )
    help_result = _run([executable, "--help"], cwd=workspace, env=env)
    if "Usage:" not in help_result.stdout or "ticket" not in help_result.stdout:
        _fail("installed wyrd --help output is incomplete")

    initialized = _json_result(
        _run(
            [executable, "init", "--name", "Release smoke", "--json"],
            cwd=workspace,
            env=env,
        ),
        "wyrd init",
    )
    if not isinstance(initialized, dict) or initialized.get("name") != "Release smoke":
        _fail(f"wyrd init returned an unexpected payload: {initialized!r}")

    created = _json_result(
        _run(
            [
                executable,
                "ticket",
                "create",
                "--title",
                "Artifact smoke ticket",
                "--body",
                "Installed artifact workflow",
                "--label",
                "release",
                "--json",
            ],
            cwd=workspace,
            env=env,
        ),
        "wyrd ticket create",
    )
    if not isinstance(created, dict) or created.get("id") != 1:
        _fail(f"ticket creation returned an unexpected payload: {created!r}")

    listed = _json_result(
        _run(
            [executable, "ticket", "list", "--summary", "--json"],
            cwd=workspace,
            env=env,
        ),
        "wyrd ticket list --summary --json",
    )
    if (
        not isinstance(listed, list)
        or len(listed) != 1
        or listed[0].get("title") != "Artifact smoke ticket"
        or listed[0].get("labels") != ["release"]
        or any(key in listed[0] for key in ("body", "created_at", "tasks"))
    ):
        _fail(f"summary JSON projection is invalid: {listed!r}")

    viewed = _json_result(
        _run([executable, "ticket", "view", "1", "--json"], cwd=workspace, env=env),
        "wyrd ticket view",
    )
    if not isinstance(viewed, dict) or viewed.get("body") != "Installed artifact workflow":
        _fail(f"ticket view returned an unexpected payload: {viewed!r}")


def _create_venv(
    uv: Path, path: Path, *, python: Path, cwd: Path, env: Mapping[str, str]
) -> None:
    _run(
        [uv, "venv", "--no-project", "--python", python, path], cwd=cwd, env=env
    )


def _install_and_smoke(
    uv: Path,
    artifact: Path,
    *,
    environment_dir: Path,
    workspace: Path,
    constraints: Path,
    env: Mapping[str, str],
    identity: ReleaseIdentity,
    python: Path,
) -> None:
    _create_venv(uv, environment_dir, python=python, cwd=workspace, env=env)
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            environment_dir / "bin" / "python",
            "--strict",
            "--constraints",
            constraints,
            artifact,
        ],
        cwd=workspace,
        env=env,
        echo_output=True,
    )
    _exercise_installed_cli(
        environment_dir, workspace=workspace, env=env, identity=identity
    )


def smoke_artifacts(
    artifacts: ArtifactPaths,
    identity: ReleaseIdentity,
    *,
    python: Path | None = None,
) -> Path:
    """Exercise direct-wheel and sdist-derived installations in clean environments."""
    uv_executable = shutil.which("uv")
    if not uv_executable:
        _fail("uv is required for isolated artifact smoke tests")
    uv = Path(uv_executable).resolve()
    python = (python or Path(sys.executable)).resolve()

    with tempfile.TemporaryDirectory(prefix="wyrd-release-smoke-") as temporary:
        temporary_root = Path(temporary).resolve()
        if temporary_root == ROOT or temporary_root.is_relative_to(ROOT):
            _fail("smoke-test temporary directory must be outside the source checkout")
        home = temporary_root / "home"
        home.mkdir()
        env = _sanitized_environment(home)
        constraints = temporary_root / "runtime-constraints.txt"
        _run(
            [
                uv,
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-annotate",
                "--no-header",
                "--output-file",
                constraints,
            ],
            cwd=ROOT,
            env=env,
        )
        if not constraints.is_file() or not constraints.read_text(encoding="utf-8").strip():
            _fail("uv export did not produce locked runtime constraints")

        wheel_workspace = temporary_root / "wheel-workspace"
        wheel_workspace.mkdir()
        _install_and_smoke(
            uv,
            artifacts.wheel,
            environment_dir=temporary_root / "wheel-environment",
            workspace=wheel_workspace,
            constraints=constraints,
            env=env,
            identity=identity,
            python=python,
        )

        build_workspace = temporary_root / "sdist-build-workspace"
        build_workspace.mkdir()
        build_environment = temporary_root / "sdist-build-environment"
        _create_venv(
            uv, build_environment, python=python, cwd=build_workspace, env=env
        )
        derived_directory = temporary_root / "sdist-derived"
        derived_directory.mkdir()
        _run(
            [
                uv,
                "build",
                "--wheel",
                "--no-sources",
                "--no-create-gitignore",
                "--python",
                build_environment / "bin" / "python",
                "--out-dir",
                derived_directory,
                artifacts.sdist,
            ],
            cwd=build_workspace,
            env=env,
            echo_output=True,
        )
        derived_entries = sorted(derived_directory.iterdir())
        if [item.name for item in derived_entries] != [identity.wheel_filename]:
            _fail(
                "sdist build must produce exactly the expected wheel; found "
                f"{[item.name for item in derived_entries]}"
            )
        derived_wheel = derived_entries[0]
        inspect_wheel(derived_wheel, identity)

        derived_workspace = temporary_root / "sdist-wheel-workspace"
        derived_workspace.mkdir()
        _install_and_smoke(
            uv,
            derived_wheel,
            environment_dir=temporary_root / "sdist-wheel-environment",
            workspace=derived_workspace,
            constraints=constraints,
            env=env,
            identity=identity,
            python=python,
        )
    return artifacts.sdist


def write_checksums(artifacts: ArtifactPaths, artifact_dir: Path) -> Path:
    manifest = checksum_path(artifact_dir)
    lines = []
    for path in (artifacts.sdist, artifacts.wheel):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    manifest.write_text("\n".join(lines) + "\n", encoding="ascii")
    print("SHA-256 digests:")
    for line in lines:
        print(line)
    print(f"Checksum manifest: {manifest}")
    return manifest


def _write_github_outputs(
    identity: ReleaseIdentity,
    artifacts: ArtifactPaths,
    artifact_dir: Path,
    manifest: Path,
) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    values = {
        "artifact_dir": artifact_dir,
        "checksum_manifest": manifest,
        "sdist": artifacts.sdist,
        "version": identity.version,
        "wheel": artifacts.wheel,
    }
    with Path(output).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            rendered = str(value)
            if "\n" in rendered or "\r" in rendered:
                _fail(f"cannot write multiline GitHub output {key!r}")
            stream.write(f"{key}={rendered}\n")


def preflight(artifact_dir: Path, *, clear: bool, tag: str | None) -> None:
    identity = validate_repository(tag=tag, check_uv=True)
    artifact_dir = prepare_artifact_directory(artifact_dir, clear=clear)
    uv = shutil.which("uv")
    if not uv:  # validate_repository already provides the useful error.
        _fail("uv is required")

    _run(
        [
            uv,
            "build",
            "--no-sources",
            "--no-create-gitignore",
            "--out-dir",
            artifact_dir,
            ROOT,
        ],
        cwd=ROOT,
        echo_output=True,
    )
    artifacts = expected_artifacts(artifact_dir, identity)

    try:
        _run(
            [
                sys.executable,
                "-m",
                "twine",
                "check",
                "--strict",
                artifacts.sdist,
                artifacts.wheel,
            ],
            cwd=ROOT,
            echo_output=True,
        )
    except ReleaseCheckError as error:
        if "No module named twine" in str(error):
            _fail(
                "Twine is unavailable; run the documented command with "
                "`uv run --locked --group release`"
            )
        raise

    inspect_sdist(artifacts.sdist, identity)
    inspect_wheel(artifacts.wheel, identity)
    smoke_artifacts(artifacts, identity)
    manifest = write_checksums(artifacts, artifact_dir)
    _write_github_outputs(identity, artifacts, artifact_dir, manifest)
    print(f"Release preflight passed for {identity.name} {identity.version}.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, validate, and smoke-test fresh Wyrd release artifacts."
    )
    parser.add_argument(
        "--artifact-dir",
        required=True,
        type=Path,
        help="caller-selected output directory (must be empty unless --clear is used)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="remove and recreate only the selected artifact directory and checksum manifest",
    )
    parser.add_argument(
        "--tag",
        help="optional production tag/ref, e.g. vX.Y.Z or refs/tags/vX.Y.Z",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        preflight(arguments.artifact_dir, clear=arguments.clear, tag=arguments.tag)
    except ReleaseCheckError as error:
        print(f"release check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
