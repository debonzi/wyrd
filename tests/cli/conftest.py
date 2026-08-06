from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.support.fake_storage import FakeStorage, FixedClock
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.presentation import PresentationDependencies, create_app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def cli_factory(storage: FakeStorage):
    def factory(
        *,
        is_tty: Callable[[object], bool] | None = None,
        confirm: Callable[[str], bool] | None = None,
        cwd: Callable[[], str] | None = None,
    ):
        application = WyrdApplication(storage, FixedClock())
        arguments = {
            "application_factory": lambda: application,
        }
        if is_tty is not None:
            arguments["is_tty"] = is_tty
        if confirm is not None:
            arguments["confirm"] = confirm
        if cwd is not None:
            arguments["cwd"] = cwd
        return create_app(PresentationDependencies(**arguments))

    return factory
