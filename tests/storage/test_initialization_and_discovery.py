from __future__ import annotations

import multiprocessing
import os
import shutil
from pathlib import Path

import pytest

from tests.storage.conftest import STORAGE_TIME
from wyrd_cli.domain.errors import (
    ConflictError,
    CorruptDataError,
    InvalidProjectError,
    NestedProjectError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
)
from wyrd_cli.infrastructure.filesystem import create_filesystem_storage


def _initialize_worker(root: str, name: str, queue) -> None:
    try:
        project = create_filesystem_storage().initialize(
            root=root, name=name, created_at=STORAGE_TIME
        )
        queue.put(("ok", project.name))
    except BaseException as error:
        queue.put(("error", type(error).__name__, str(error)))


def test_initialize_publishes_exact_complete_layout_and_is_idempotent(tmp_path: Path) -> None:
    storage = create_filesystem_storage()
    project = storage.initialize(
        root=str(tmp_path), name="Example project", created_at=STORAGE_TIME
    )

    assert project.root == str(tmp_path)
    assert sorted(path.name for path in (tmp_path / ".wyrd").iterdir()) == [
        "lock",
        "project.yaml",
        "tickets",
    ]
    assert (tmp_path / ".wyrd" / "lock").read_bytes() == b""
    assert (tmp_path / ".wyrd" / "project.yaml").read_bytes() == (
        b"schema_version: 1\n"
        b"name: Example project\n"
        b'created_at: "2026-08-05T18:59:36Z"\n'
    )
    assert not (tmp_path / ".gitignore").exists()

    again = create_filesystem_storage().initialize(
        root=str(tmp_path), name="Example project", created_at=STORAGE_TIME
    )
    assert again == project
    with pytest.raises(ConflictError):
        create_filesystem_storage().initialize(
            root=str(tmp_path), name="Other", created_at=STORAGE_TIME
        )


def test_omitted_name_is_idempotent_even_if_existing_name_differs_from_basename(
    tmp_path: Path,
) -> None:
    from tests.support.fake_storage import FixedClock
    from wyrd_cli.application.services import WyrdApplication

    first = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    first.initialize(str(tmp_path), "Custom immutable name")
    second = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    assert second.initialize(str(tmp_path)).name == "Custom immutable name"


def test_concurrent_initialization_has_one_complete_winner(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_initialize_worker,
            args=(str(tmp_path), "Same project", queue),
        )
        for _ in range(6)
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert results == [("ok", "Same project")] * 6
    project = create_filesystem_storage().discover(str(tmp_path))
    assert project.name == "Same project"
    assert sorted((tmp_path / ".wyrd").iterdir()) == sorted(
        [
            tmp_path / ".wyrd" / "lock",
            tmp_path / ".wyrd" / "project.yaml",
            tmp_path / ".wyrd" / "tickets",
        ]
    )
    assert not list(tmp_path.glob(".wyrd-init-*.tmp"))


def test_discovery_walks_ancestors_and_rejects_nested_projects(tmp_path: Path) -> None:
    root = tmp_path / "root"
    descendant = root / "a" / "b"
    descendant.mkdir(parents=True)
    create_filesystem_storage().initialize(
        root=str(root), name="Outer", created_at=STORAGE_TIME
    )
    discovered = create_filesystem_storage().discover(str(descendant))
    assert discovered.root == str(root)

    nested_root = root / "a"
    shutil.copytree(root / ".wyrd", nested_root / ".wyrd")
    with pytest.raises(NestedProjectError):
        create_filesystem_storage().discover(str(descendant))
    with pytest.raises(NestedProjectError):
        create_filesystem_storage().initialize(
            root=str(nested_root), name="Outer", created_at=STORAGE_TIME
        )


def test_initialize_refuses_an_ancestor_project_without_creating_state(tmp_path: Path) -> None:
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    create_filesystem_storage().initialize(
        root=str(root), name="Outer", created_at=STORAGE_TIME
    )
    with pytest.raises(ProjectAlreadyExistsError):
        create_filesystem_storage().initialize(
            root=str(child), name="Child", created_at=STORAGE_TIME
        )
    assert not (child / ".wyrd").exists()


def test_discovery_does_not_skip_an_invalid_nearest_candidate(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    create_filesystem_storage().initialize(
        root=str(outer), name="Outer", created_at=STORAGE_TIME
    )
    (inner / ".wyrd").mkdir()

    with pytest.raises(NestedProjectError):
        create_filesystem_storage().discover(str(inner))

    shutil.rmtree(outer / ".wyrd")
    with pytest.raises(InvalidProjectError):
        create_filesystem_storage().discover(str(inner))


def test_no_project_is_distinct_from_a_corrupt_candidate(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        create_filesystem_storage().discover(str(tmp_path))

    (tmp_path / ".wyrd").mkdir()
    with pytest.raises(InvalidProjectError):
        create_filesystem_storage().discover(str(tmp_path))


def test_project_root_symlink_is_rejected_but_unrelated_ancestor_symlink_is_allowed(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    create_filesystem_storage().initialize(
        root=str(real), name="Real", created_at=STORAGE_TIME
    )
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(InvalidProjectError):
        create_filesystem_storage().discover(str(root_link))

    ancestor = tmp_path / "ancestor"
    target = tmp_path / "target"
    target.mkdir()
    ancestor.symlink_to(target, target_is_directory=True)
    project = ancestor / "project"
    project.mkdir()
    create_filesystem_storage().initialize(
        root=str(project), name="Via ancestor", created_at=STORAGE_TIME
    )
    assert create_filesystem_storage().discover(str(project)).name == "Via ancestor"


@pytest.mark.parametrize(
    "relative",
    [
        ".wyrd",
        ".wyrd/lock",
        ".wyrd/tickets",
        ".wyrd/tickets/1",
        ".wyrd/tickets/1/ticket.md",
        ".wyrd/tickets/1/tasks",
        ".wyrd/tickets/1/tasks/1.md",
    ],
)
def test_every_managed_level_rejects_symlinks(
    initialized_project, relative: str
) -> None:
    root, _, application = initialized_project
    from wyrd_cli.application.dto import CreateResourceRequest

    application.create_ticket(CreateResourceRequest(title="Ticket"))
    application.create_task(1, CreateResourceRequest(title="Task"))
    path = root / relative
    moved = root / ("moved-" + relative.replace("/", "-"))
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=moved.is_dir())

    with pytest.raises(InvalidProjectError):
        storage = create_filesystem_storage()
        storage.discover(str(root))
        with storage.read() as transaction:
            transaction.list_tickets()


def test_missing_ticket_file_is_corruption_not_absence(initialized_project) -> None:
    root, _, application = initialized_project
    from wyrd_cli.application.dto import CreateResourceRequest

    application.create_ticket(CreateResourceRequest(title="Ticket"))
    (root / ".wyrd/tickets/1/ticket.md").unlink()
    storage = create_filesystem_storage()
    storage.discover(str(root))
    with pytest.raises(CorruptDataError):
        with storage.read() as transaction:
            transaction.list_tickets()
