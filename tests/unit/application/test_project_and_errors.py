from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.errors import (
    ConflictError,
    DomainValidationError,
    InvalidProjectError,
    StorageTransactionError,
)
from tests.support.fake_storage import FakeStorage, FixedClock

CREATED = datetime(2026, 8, 6, 10, 20, 30, tzinfo=UTC)


def test_initialize_validates_explicit_name_and_returns_project_projection() -> None:
    storage = FakeStorage()
    storage.project = storage.project.model_copy(update={"root": "/other"})
    clock = FixedClock(CREATED)
    result = WyrdApplication(storage, clock).initialize("/new", "  New project  ")
    assert result.model_dump() == {
        "schema_version": 1,
        "name": "New project",
        "created_at": CREATED,
        "root": "/new",
    }
    assert clock.calls == 1
    assert storage.calls == ["initialize"]


def test_initialize_uses_storage_suggested_basename_and_preserves_conflict() -> None:
    storage = FakeStorage()
    storage.project = storage.project.model_copy(update={"root": "/other"})
    app = WyrdApplication(storage, FixedClock(CREATED))
    assert app.initialize("/work/my-project").name == "my-project"
    assert storage.calls[:2] == ["suggested_project_name", "initialize"]

    with pytest.raises(ConflictError):
        app.initialize("/work/my-project", "different")


def test_discovery_errors_are_not_reclassified() -> None:
    storage = FakeStorage()
    storage.injected_errors["discover"] = InvalidProjectError("unsafe project")
    with pytest.raises(InvalidProjectError) as caught:
        WyrdApplication(storage, FixedClock()).discover_project("/start")
    assert caught.value.code == "invalid_project"


def test_storage_transaction_errors_cross_boundary_unchanged() -> None:
    storage = FakeStorage()
    storage.injected_errors["read"] = StorageTransactionError("cannot read")
    with pytest.raises(StorageTransactionError):
        WyrdApplication(storage, FixedClock()).project_status()


def test_invalid_lock_timeout_never_opens_transaction() -> None:
    storage = FakeStorage()
    app = WyrdApplication(storage, FixedClock())
    for timeout in (-1, float("inf"), float("nan"), True):
        with pytest.raises(DomainValidationError):
            app.project_status(lock_timeout=timeout)
    assert storage.read_transactions == 0
