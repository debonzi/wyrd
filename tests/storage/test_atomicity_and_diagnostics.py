from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.storage.conftest import STORAGE_TIME
from tests.support.factories import ticket
from tests.support.fake_storage import FixedClock
from wyrd_cli.application.dto import CreateResourceRequest, EditResourceRequest
from wyrd_cli.application.services import WyrdApplication
from wyrd_cli.domain.errors import CorruptDataError, InvalidProjectError, StorageTransactionError
from wyrd_cli.domain.models import TaskIdentity
from wyrd_cli.infrastructure.filesystem import create_filesystem_storage
from wyrd_cli.infrastructure.filesystem.codec import encode_ticket


class FailOnce:
    def __init__(self, point: str, *, path_name: str | None = None) -> None:
        self.point = point
        self.path_name = path_name
        self.failed = False

    def __call__(self, point: str, path: Path) -> None:
        if (
            not self.failed
            and point == self.point
            and (self.path_name is None or path.name == self.path_name)
        ):
            self.failed = True
            raise OSError(f"injected {point} failure")


def _bound_fault_application(root: Path, fault: FailOnce) -> WyrdApplication:
    storage = create_filesystem_storage(fault_injector=fault)
    application = WyrdApplication(storage, FixedClock(STORAGE_TIME))
    application.discover_project(str(root))
    return application


def _canonical_fingerprint(root: Path) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for path in sorted((root / ".wyrd").rglob("*")):
        relative = path.relative_to(root / ".wyrd").as_posix()
        if path.is_file() and path.name != "lock":
            result[relative] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
    return result


@pytest.mark.parametrize("point", ["open", "write", "flush", "file_fsync", "link"])
def test_task_create_fault_before_publication_never_exposes_partial_canonical(
    initialized_project, point: str
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Parent"))
    fault = FailOnce(point)
    with pytest.raises(StorageTransactionError):
        _bound_fault_application(root, fault).create_task(
            1, CreateResourceRequest(title="Child")
        )
    assert fault.failed
    assert not (root / ".wyrd/tickets/1/tasks/1.md").exists()
    assert create_filesystem_storage().discover(str(root)).name == "Example project"


def test_directory_fsync_failure_after_task_publication_is_complete_not_partial(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Parent"))
    fault = FailOnce("directory_fsync", path_name="tasks")
    with pytest.raises(StorageTransactionError):
        _bound_fault_application(root, fault).create_task(
            1, CreateResourceRequest(title="Child")
        )
    destination = root / ".wyrd/tickets/1/tasks/1.md"
    assert destination.exists()
    storage = create_filesystem_storage()
    storage.discover(str(root))
    with storage.read() as transaction:
        assert transaction.get_task(
            TaskIdentity(ticket_id=1, number=1)
        ).title == "Child"


@pytest.mark.parametrize("point", ["write", "flush", "file_fsync", "replace"])
def test_update_fault_preserves_whole_old_canonical_before_replace(
    initialized_project, point: str
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Original", body="old"))
    destination = root / ".wyrd/tickets/1/ticket.md"
    old_bytes = destination.read_bytes()
    fault = FailOnce(point)
    with pytest.raises(StorageTransactionError):
        _bound_fault_application(root, fault).edit_ticket(
            1, EditResourceRequest(title="Updated", body="new")
        )
    assert destination.read_bytes() == old_bytes
    assert create_filesystem_storage().discover(str(root)).name == "Example project"


def test_update_directory_fsync_failure_leaves_a_complete_new_canonical(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Original"))
    fault = FailOnce("directory_fsync", path_name="1")
    with pytest.raises(StorageTransactionError):
        _bound_fault_application(root, fault).edit_ticket(
            1, EditResourceRequest(title="Updated")
        )
    app = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    result = app.view_ticket(1)
    assert result.title == "Updated"
    assert result.revision == 2


def test_cleanup_failure_leaves_only_recognizable_temp_and_complete_task(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Parent"))
    fault = FailOnce("cleanup")
    with pytest.raises(StorageTransactionError):
        _bound_fault_application(root, fault).create_task(
            1, CreateResourceRequest(title="Child")
        )
    assert (root / ".wyrd/tickets/1/tasks/1.md").exists()
    temps = list((root / ".wyrd/tickets/1/tasks").glob(".wyrd-tmp-*.tmp"))
    assert len(temps) == 1

    storage = create_filesystem_storage()
    app = WyrdApplication(storage, FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    assert app.view_task(TaskIdentity(ticket_id=1, number=1)).title == "Child"
    assert any(problem.code == "stale_temporary" for problem in app.doctor().problems)


def test_ticket_publication_fault_never_exposes_incomplete_aggregate(
    initialized_project,
) -> None:
    root, _, _ = initialized_project
    fault = FailOnce("rename")
    with pytest.raises(StorageTransactionError):
        _bound_fault_application(root, fault).create_ticket(
            CreateResourceRequest(title="Ticket")
        )
    assert not (root / ".wyrd/tickets/1").exists()
    assert not list((root / ".wyrd/tickets").glob(".wyrd-tmp-*.tmp"))


def test_init_faults_never_publish_partial_project(tmp_path: Path) -> None:
    for point in ("write", "flush", "file_fsync", "rename"):
        root = tmp_path / point
        root.mkdir()
        fault = FailOnce(point)
        with pytest.raises(StorageTransactionError):
            create_filesystem_storage(fault_injector=fault).initialize(
                root=str(root), name="Project", created_at=STORAGE_TIME
            )
        assert not (root / ".wyrd").exists()
        assert not list(root.glob(".wyrd-init-*.tmp"))


def test_init_root_fsync_failure_may_report_error_but_publishes_only_complete_tree(
    tmp_path: Path,
) -> None:
    fault = FailOnce("directory_fsync", path_name=tmp_path.name)
    with pytest.raises(StorageTransactionError):
        create_filesystem_storage(fault_injector=fault).initialize(
            root=str(tmp_path), name="Project", created_at=STORAGE_TIME
        )
    project = create_filesystem_storage().discover(str(tmp_path))
    assert project.name == "Project"


def test_exceptional_transaction_exit_discards_an_unpublished_mutation(
    initialized_project,
) -> None:
    root, storage, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Original"))
    from wyrd_cli.domain.models import replace_ticket

    with pytest.raises(RuntimeError):
        with storage.write() as transaction:
            original = transaction.get_ticket(1)
            transaction.update_ticket(
                replace_ticket(original, title="Should not commit", revision=2),
                expected_revision=1,
            )
            raise RuntimeError("abort")

    assert (root / ".wyrd/tickets/1/ticket.md").read_bytes() == encode_ticket(
        ticket(1, title="Original")
    )


def test_atomic_create_if_absent_never_overwrites_existing_task(initialized_project) -> None:
    root, storage, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Parent"))
    created = application.create_task(1, CreateResourceRequest(title="Original"))
    old_bytes = (root / ".wyrd/tickets/1/tasks/1.md").read_bytes()
    from tests.support.factories import task
    from wyrd_cli.domain.errors import StorageConflictError

    with storage.write() as transaction:
        with pytest.raises(StorageConflictError):
            transaction.create_task(task(1, 1, title="Replacement"))
    assert created.title == "Original"
    assert (root / ".wyrd/tickets/1/tasks/1.md").read_bytes() == old_bytes


def test_doctor_is_tolerant_deterministic_and_read_only(initialized_project) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="One"))
    application.create_ticket(CreateResourceRequest(title="Two"))
    one = root / ".wyrd/tickets/1/ticket.md"
    two = root / ".wyrd/tickets/2/ticket.md"
    one.write_bytes(one.read_bytes().replace(b"revision: 1\n", b"revision: 1\nrevision: 2\n"))
    two.write_bytes(two.read_bytes().replace(b"title: Two\n", b"title: Two\nunknown: true\n"))
    (root / ".wyrd/tickets/not-an-id").mkdir()
    (root / ".wyrd/tickets/.wyrd-tmp-0123456789abcdef0123456789abcdef.tmp").write_text(
        "stale", encoding="utf-8"
    )
    before = _canonical_fingerprint(root)

    fresh = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    fresh.discover_project(str(root))
    report = fresh.doctor()

    after = _canonical_fingerprint(root)
    assert not report.healthy
    codes = {problem.code for problem in report.problems}
    assert {"duplicate_key", "unknown_field", "unexpected_path", "stale_temporary"} <= codes
    assert [(problem.path, problem.code) for problem in report.problems] == sorted(
        (problem.path, problem.code) for problem in report.problems
    )
    assert before == after
    with pytest.raises(CorruptDataError):
        fresh.project_status()


def test_fresh_process_doctor_can_report_a_malformed_project_file(
    initialized_project,
) -> None:
    root, _, _ = initialized_project
    project = root / ".wyrd/project.yaml"
    project.write_bytes(project.read_bytes() + b"name: duplicate\n")
    app = WyrdApplication(create_filesystem_storage(), FixedClock(STORAGE_TIME))
    app.bind_doctor_project(str(root))
    report = app.doctor()
    assert any(
        problem.path == "project.yaml" and problem.code == "duplicate_key"
        for problem in report.problems
    )


def test_doctor_does_not_call_a_malformed_existing_identity_missing(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Blocker"))
    application.create_ticket(CreateResourceRequest(title="Blocked"))
    blocker = root / ".wyrd/tickets/1/ticket.md"
    blocker.write_bytes(
        blocker.read_bytes().replace(
            b"revision: 1\n", b"revision: 1\nunknown: true\n"
        )
    )
    blocked = root / ".wyrd/tickets/2/ticket.md"
    blocked.write_bytes(
        blocked.read_bytes().replace(b"blocked_by: []\n", b"blocked_by:\n  - 1\n")
    )

    report = application.doctor()
    assert any(problem.code == "unknown_field" for problem in report.problems)
    assert not any(
        problem.code == "dependency_target_not_found"
        and problem.details.get("blocker_id") == 1
        for problem in report.problems
    )


def test_doctor_reports_symlink_without_following_it(initialized_project, tmp_path: Path) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Ticket"))
    canonical = root / ".wyrd/tickets/1/ticket.md"
    outside = tmp_path / "outside.md"
    outside.write_bytes(canonical.read_bytes())
    canonical.unlink()
    canonical.symlink_to(outside)

    report = application.doctor()
    assert any(
        problem.path == "tickets/1/ticket.md" and problem.code == "symlink"
        for problem in report.problems
    )
    assert outside.read_bytes() == encode_ticket(ticket(1, title="Ticket"))


def test_doctor_with_invalid_lock_fails_ordinarily_and_never_repairs(initialized_project) -> None:
    root, _, application = initialized_project
    lock = root / ".wyrd/lock"
    lock.unlink()
    with pytest.raises(InvalidProjectError):
        application.doctor()
    assert not lock.exists()


def test_normal_mutation_changes_only_selected_canonical_file(initialized_project) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="One"))
    application.create_ticket(CreateResourceRequest(title="Two"))
    application.create_task(1, CreateResourceRequest(title="Child"))
    before = _canonical_fingerprint(root)
    application.edit_ticket(1, EditResourceRequest(title="Changed"))
    after = _canonical_fingerprint(root)
    changed = {path for path in before if before[path] != after[path]}
    assert changed == {"tickets/1/ticket.md"}


def test_private_temps_are_ignored_by_normal_scans_but_reported_by_doctor(
    initialized_project,
) -> None:
    root, _, application = initialized_project
    temp = root / ".wyrd/tickets/.wyrd-tmp-0123456789abcdef0123456789abcdef.tmp"
    temp.write_bytes(b"partial")
    assert application.project_status().tickets.total == 0
    assert any(problem.code == "stale_temporary" for problem in application.doctor().problems)
