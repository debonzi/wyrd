from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.storage.conftest import STORAGE_TIME
from tests.support.factories import task, ticket
from wyrd_cli.application.dto import CreateResourceRequest
from wyrd_cli.domain.errors import CorruptDataError, UnsupportedSchemaError
from wyrd_cli.infrastructure.filesystem import create_filesystem_storage
from wyrd_cli.infrastructure.filesystem.codec import (
    CodecFailure,
    decode_project,
    decode_task,
    decode_ticket,
    encode_project,
    encode_task,
    encode_ticket,
)
from wyrd_cli.domain.models import Project


def test_ticket_and_task_codec_have_deterministic_byte_round_trips() -> None:
    item = ticket(
        12,
        title="Fix startup race",
        body="Markdown description.\n---\nstill body\n",
        labels=("bug", "priority:high"),
        blocked_by=(8,),
    )
    encoded = encode_ticket(item)
    assert encoded == (
        b"---\n"
        b"id: 12\n"
        b"revision: 1\n"
        b"title: Fix startup race\n"
        b"status: open\n"
        b"labels:\n"
        b"  - bug\n"
        b"  - priority:high\n"
        b"blocked_by:\n"
        b"  - 8\n"
        b'created_at: "2026-08-05T18:59:36Z"\n'
        b'updated_at: "2026-08-05T18:59:36Z"\n'
        b"closed_at: null\n"
        b"---\n"
        b"Markdown description.\n---\nstill body\n"
    )
    assert encode_ticket(decode_ticket(encoded, expected_id=12)) == encoded

    child = task(12, 3, body="")
    task_bytes = encode_task(child)
    decoded = decode_task(task_bytes, ticket_id=12, expected_number=3)
    assert decoded.public_id == "12.3"
    assert decoded.body == ""
    assert encode_task(decoded) == task_bytes


def test_project_codec_is_safe_strict_and_rejects_duplicate_or_unknown_fields() -> None:
    project = Project(
        root="/project", name="Example", created_at=STORAGE_TIME
    )
    assert decode_project(encode_project(project), root="/project") == project

    with pytest.raises(CodecFailure) as duplicate:
        decode_project(
            b"schema_version: 1\nname: one\nname: two\ncreated_at: x\n",
            root="/project",
        )
    assert duplicate.value.code == "duplicate_key"

    with pytest.raises(CodecFailure) as unknown:
        decode_project(
            b'schema_version: 1\nname: one\ncreated_at: "2026-08-05T18:59:36Z"\nextra: true\n',
            root="/project",
        )
    assert unknown.value.code == "unknown_field"

    with pytest.raises(CodecFailure) as boolean:
        decode_project(
            b'schema_version: true\nname: one\ncreated_at: "2026-08-05T18:59:36Z"\n',
            root="/project",
        )
    assert boolean.value.code == "invalid_schema_version"


def test_nested_duplicate_mapping_keys_are_rejected_by_safe_loader() -> None:
    with pytest.raises(CodecFailure) as caught:
        decode_project(b"outer:\n  key: one\n  key: two\n", root="/project")
    assert caught.value.code == "duplicate_key"


def test_front_matter_boundaries_line_endings_and_utf8_are_exact() -> None:
    original = encode_ticket(ticket(1, body="a\n---\nb\n"))
    assert decode_ticket(original, expected_id=1).body == "a\n---\nb\n"

    with pytest.raises(CodecFailure) as missing_start:
        decode_ticket(original.removeprefix(b"---\n"), expected_id=1)
    assert missing_start.value.code == "invalid_front_matter"

    with pytest.raises(CodecFailure) as crlf:
        decode_ticket(original.replace(b"\n", b"\r\n"), expected_id=1)
    assert crlf.value.code == "invalid_line_endings"

    with pytest.raises(CodecFailure) as utf8:
        decode_ticket(original + b"\xff", expected_id=1)
    assert utf8.value.code == "invalid_utf8"


def test_strict_persisted_resource_validation_classifies_distinct_failures() -> None:
    canonical = encode_ticket(ticket(1, labels=("a", "z")))
    cases = {
        "duplicate_key": canonical.replace(b"revision: 1\n", b"revision: 1\nrevision: 2\n"),
        "unknown_field": canonical.replace(b"status: open\n", b"status: open\nextra: value\n"),
        "id_path_mismatch": canonical.replace(b"id: 1\n", b"id: 2\n"),
        "duplicate_list_item": canonical.replace(b"  - z\n", b"  - a\n"),
        "unsorted_list": canonical.replace(b"  - a\n  - z\n", b"  - z\n  - a\n"),
        "invalid_label": canonical.replace(b"  - a\n", b"  - BAD\n"),
    }
    for expected_code, data in cases.items():
        with pytest.raises(CodecFailure) as caught:
            decode_ticket(data, expected_id=1)
        assert caught.value.code == expected_code


def test_ordinary_scan_is_strict_and_schema_version_is_distinct(initialized_project) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="Ticket"))
    ticket_path = root / ".wyrd/tickets/1/ticket.md"
    ticket_path.write_bytes(
        ticket_path.read_bytes().replace(
            b"revision: 1\n", b"revision: 1\nunknown: true\n"
        )
    )
    storage = create_filesystem_storage()
    storage.discover(str(root))
    with pytest.raises(CorruptDataError):
        with storage.read() as transaction:
            transaction.list_tickets()

    ticket_path.write_bytes(encode_ticket(ticket(1, title="Ticket")))
    project_path = root / ".wyrd/project.yaml"
    project_path.write_bytes(project_path.read_bytes().replace(b"1\n", b"2\n", 1))
    with pytest.raises(UnsupportedSchemaError):
        create_filesystem_storage().discover(str(root))


def test_max_plus_one_scans_gaps_and_never_creates_indexes(initialized_project) -> None:
    root, _, application = initialized_project
    application.create_ticket(CreateResourceRequest(title="One"))
    ticket_one = root / ".wyrd/tickets/1"
    ticket_seven = root / ".wyrd/tickets/7"
    ticket_seven.mkdir()
    (ticket_seven / "tasks").mkdir()
    (ticket_seven / "ticket.md").write_bytes(
        encode_ticket(ticket(7, title="Seven"))
    )
    (ticket_one / "tasks/4.md").write_bytes(
        encode_task(task(1, 4, title="Four"))
    )

    storage = create_filesystem_storage()
    from tests.support.fake_storage import FixedClock
    from wyrd_cli.application.services import WyrdApplication

    app = WyrdApplication(storage, FixedClock(STORAGE_TIME))
    app.discover_project(str(root))
    assert app.create_ticket(CreateResourceRequest(title="Eight")).id == 8
    assert app.create_task(1, CreateResourceRequest(title="Five")).id == "1.5"

    all_names = {path.name for path in (root / ".wyrd").rglob("*")}
    assert not all_names & {"index", "index.yaml", "counter", "backup", "migrations"}
