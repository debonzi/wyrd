from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class CliResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def json_stdout(self):
        return json.loads(self.stdout)

    def json_stderr(self):
        return json.loads(self.stderr)


class WyrdProcess:
    """Run the installed console script with isolated process state."""

    def __init__(self, *, executable: Path, home: Path) -> None:
        self.executable = executable
        self.home = home
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(home),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "NO_COLOR": "1",
                "PYTHONUTF8": "1",
            }
        )
        for name in ("FORCE_COLOR", "PYTHONPATH", "PYTHONSTARTUP"):
            self.env.pop(name, None)

    def run(
        self,
        cwd: Path,
        *args: str,
        input: str | None = None,
        timeout: float = 15,
    ) -> CliResult:
        completed = subprocess.run(
            [str(self.executable), *args],
            cwd=cwd,
            env=self.env,
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout,
            check=False,
        )
        return CliResult(tuple(args), completed.returncode, completed.stdout, completed.stderr)

    def popen(
        self,
        cwd: Path,
        *args: str,
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text: bool = True,
    ) -> subprocess.Popen:
        return subprocess.Popen(
            [str(self.executable), *args],
            cwd=cwd,
            env=self.env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            encoding="utf-8" if text else None,
        )


@pytest.fixture
def wyrd_process(tmp_path: Path) -> WyrdProcess:
    executable = Path(sys.executable).with_name("wyrd")
    assert executable.is_file(), f"installed console script is missing: {executable}"
    return WyrdProcess(executable=executable, home=tmp_path / "home")


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def assert_json_document(text: str):
    """Assert the one-value/newline contract and recursive key ordering."""

    assert text.endswith("\n")
    assert text.count("\n") == 1
    value = json.loads(text)
    _assert_sorted_keys(value)
    return value


def _assert_sorted_keys(value) -> None:
    if isinstance(value, dict):
        assert list(value) == sorted(value)
        for item in value.values():
            _assert_sorted_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_sorted_keys(item)
