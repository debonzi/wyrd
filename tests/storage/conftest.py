from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support.fake_storage import FixedClock
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.infrastructure.filesystem import FilesystemStorage, create_filesystem_storage

STORAGE_TIME = datetime(2026, 8, 5, 18, 59, 36, tzinfo=UTC)


@pytest.fixture
def initialized_project(tmp_path: Path) -> tuple[Path, FilesystemStorage, WyrdApplication]:
    storage = create_filesystem_storage()
    application = WyrdApplication(storage, FixedClock(STORAGE_TIME))
    application.initialize(str(tmp_path), "Example project")
    return tmp_path, storage, application


def bind_application(root: Path) -> tuple[FilesystemStorage, WyrdApplication]:
    storage = create_filesystem_storage()
    application = WyrdApplication(storage, FixedClock(STORAGE_TIME))
    application.discover_project(str(root))
    return storage, application
