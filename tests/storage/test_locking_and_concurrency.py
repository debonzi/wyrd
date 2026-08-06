from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from tests.storage.conftest import STORAGE_TIME
from tests.support.fake_storage import FixedClock
from wyrd_cli.application.dto import CreateResourceRequest, EditResourceRequest
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.errors import InvalidProjectError, LockTimeoutError
from wyrd_cli.infrastructure.filesystem import create_filesystem_storage


def _hold_lock(root: str, writable: bool, acquired, release, queue) -> None:
    try:
        storage = create_filesystem_storage()
        storage.discover(root)
        manager = storage.write(timeout=5) if writable else storage.read(timeout=5)
        with manager:
            acquired.set()
            release.wait(10)
        queue.put(("ok",))
    except BaseException as error:
        queue.put(("error", type(error).__name__, str(error)))


def _create_ticket_worker(root: str, number: int, queue) -> None:
    try:
        storage = create_filesystem_storage()
        app = WyrdApplication(storage, FixedClock(STORAGE_TIME))
        app.discover_project(root)
        result = app.create_ticket(
            CreateResourceRequest(title=f"Concurrent {number}"), lock_timeout=10
        )
        queue.put(("ok", result.id))
    except BaseException as error:
        queue.put(("error", type(error).__name__, str(error)))


def _create_task_worker(root: str, number: int, queue) -> None:
    try:
        storage = create_filesystem_storage()
        app = WyrdApplication(storage, FixedClock(STORAGE_TIME))
        app.discover_project(root)
        result = app.create_task(
            1,
            CreateResourceRequest(title=f"Concurrent task {number}"),
            lock_timeout=10,
        )
        queue.put(("ok", result.id))
    except BaseException as error:
        queue.put(("error", type(error).__name__, str(error)))


def _create_task_for_ticket_worker(
    root: str, ticket_id: int, number: int, queue
) -> None:
    try:
        storage = create_filesystem_storage()
        app = WyrdApplication(storage, FixedClock(STORAGE_TIME))
        app.discover_project(root)
        result = app.create_task(
            ticket_id,
            CreateResourceRequest(title=f"Task {ticket_id}-{number}"),
            lock_timeout=10,
        )
        queue.put(("ok", result.id))
    except BaseException as error:
        queue.put(("error", type(error).__name__, str(error)))


def _edit_label_worker(root: str, label: str, queue) -> None:
    try:
        storage = create_filesystem_storage()
        app = WyrdApplication(storage, FixedClock(STORAGE_TIME))
        app.discover_project(root)
        result = app.edit_ticket(
            1, EditResourceRequest(add_labels=(label,)), lock_timeout=10
        )
        queue.put(("ok", result.revision))
    except BaseException as error:
        queue.put(("error", type(error).__name__, str(error)))


def _run_workers(target, argument_rows: list[tuple]) -> list[tuple]:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=target, args=(*arguments, queue))
        for arguments in argument_rows
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    return results


def test_shared_readers_coexist_and_writer_times_out(initialized_project) -> None:
    root, _, _ = initialized_project
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    queue = context.Queue()
    holder = context.Process(
        target=_hold_lock,
        args=(str(root), False, acquired, release, queue),
    )
    holder.start()
    assert acquired.wait(10)
    try:
        storage = create_filesystem_storage()
        storage.discover(str(root))
        with storage.read(timeout=0):
            pass
        with pytest.raises(LockTimeoutError):
            with storage.write(timeout=0.05):
                pass
    finally:
        release.set()
        assert queue.get(timeout=10) == ("ok",)
        holder.join(timeout=10)
        assert holder.exitcode == 0


def test_exclusive_writer_blocks_both_reader_and_writer(initialized_project) -> None:
    root, _, _ = initialized_project
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    queue = context.Queue()
    holder = context.Process(
        target=_hold_lock,
        args=(str(root), True, acquired, release, queue),
    )
    holder.start()
    assert acquired.wait(10)
    try:
        storage = create_filesystem_storage()
        storage.discover(str(root))
        with pytest.raises(LockTimeoutError):
            with storage.read(timeout=0.03):
                pass
        with pytest.raises(LockTimeoutError):
            with storage.write(timeout=0):
                pass
    finally:
        release.set()
        assert queue.get(timeout=10) == ("ok",)
        holder.join(timeout=10)


def test_lock_is_released_after_exception(initialized_project) -> None:
    _, storage, _ = initialized_project
    with pytest.raises(RuntimeError):
        with storage.write(timeout=0):
            raise RuntimeError("boom")
    with storage.write(timeout=0):
        pass


def test_missing_symlink_or_nonempty_lock_is_not_repaired(initialized_project) -> None:
    root, storage, _ = initialized_project
    lock = root / ".wyrd/lock"
    lock.unlink()
    with pytest.raises(InvalidProjectError):
        with storage.read(timeout=0):
            pass
    assert not lock.exists()

    lock.write_text("owner record is forbidden", encoding="utf-8")
    with pytest.raises(InvalidProjectError):
        with storage.read(timeout=0):
            pass
    assert lock.read_text(encoding="utf-8") == "owner record is forbidden"


def test_multiprocess_ticket_creation_allocates_unique_ids(initialized_project) -> None:
    root, _, _ = initialized_project
    count = 12
    results = _run_workers(
        _create_ticket_worker, [(str(root), number) for number in range(count)]
    )
    assert all(result[0] == "ok" for result in results), results
    ids = sorted(result[1] for result in results)
    assert ids == list(range(1, count + 1))

    app = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    assert [item.id for item in app.list_tickets()] == ids


def test_multiprocess_task_creation_allocates_unique_local_numbers(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Parent"))
    count = 12
    results = _run_workers(
        _create_task_worker, [(str(root), number) for number in range(count)]
    )
    assert all(result[0] == "ok" for result in results), results
    ids = sorted((result[1] for result in results), key=lambda value: int(value.split(".")[1]))
    assert ids == [f"1.{number}" for number in range(1, count + 1)]

    app = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    assert [item.id for item in app.list_tasks(1)] == ids


def test_concurrent_task_creates_in_distinct_tickets_remain_valid(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="First"))
    application.create_ticket(CreateResourceRequest(title="Second"))
    arguments = [
        (str(root), ticket_id, number)
        for ticket_id in (1, 2)
        for number in range(5)
    ]
    results = _run_workers(_create_task_for_ticket_worker, arguments)
    assert all(result[0] == "ok" for result in results), results

    app = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    assert [item.id for item in app.list_tasks(1)] == [f"1.{n}" for n in range(1, 6)]
    assert [item.id for item in app.list_tasks(2)] == [f"2.{n}" for n in range(1, 6)]


def test_concurrent_edits_reload_and_preserve_every_committed_change(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Editable"))
    labels = [f"p{number}" for number in range(8)]
    results = _run_workers(
        _edit_label_worker, [(str(root), label) for label in labels]
    )
    assert all(result[0] == "ok" for result in results), results

    app = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    result = app.view_ticket(1)
    assert result.labels == tuple(sorted(labels))
    assert result.revision == 1 + len(labels)
