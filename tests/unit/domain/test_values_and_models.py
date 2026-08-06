from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from wyrd_cli.domain.errors import (
    DomainValidationError,
    IdentitySyntaxError,
    InvalidLabelError,
)
from wyrd_cli.domain.models import Project, ResourceStatus, Task, TaskIdentity, Ticket
from wyrd_cli.domain.values import (
    format_task_id,
    normalize_body,
    normalize_labels,
    parse_task_id,
    parse_ticket_id,
    validate_project_name,
    validate_title,
)

NOW = datetime(2026, 8, 5, 18, 59, 36, tzinfo=UTC)


@pytest.mark.parametrize("value, expected", [("1", 1), ("12", 12), ("999999", 999999)])
def test_ticket_id_exact_parsing(value: str, expected: int) -> None:
    assert parse_ticket_id(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "0", "00", "01", "#12", "+12", "-12", " 12", "12 ", "1.0", "１２"],
)
def test_ticket_id_rejects_noncanonical_syntax(value: str) -> None:
    with pytest.raises(IdentitySyntaxError):
        parse_ticket_id(value)


@pytest.mark.parametrize(
    "value, expected",
    [("1.1", (1, 1)), ("12.10", (12, 10)), ("999.2", (999, 2))],
)
def test_task_id_round_trip(value: str, expected: tuple[int, int]) -> None:
    identity = parse_task_id(value)
    assert (identity.ticket_id, identity.number) == expected
    assert identity.public_id == value
    assert format_task_id(*expected) == value


@pytest.mark.parametrize(
    "value",
    ["", "1", "1.", ".1", "0.1", "1.0", "01.1", "1.01", "+1.2", "1.2 ", "1.2.3"],
)
def test_task_id_rejects_noncanonical_syntax(value: str) -> None:
    with pytest.raises(IdentitySyntaxError):
        parse_task_id(value)


def test_decimal_task_numbers_do_not_collapse() -> None:
    assert parse_task_id("12.10") != parse_task_id("12.1")


@pytest.mark.parametrize(
    "label",
    ["a", "0", "bug", "priority:high", "good_first_task", "a" * 20, "9:_"],
)
def test_valid_labels(label: str) -> None:
    assert normalize_labels((label, label)) == (label,)


@pytest.mark.parametrize(
    "label",
    ["", "A", "Bug", "has space", "-bad", ":bad", "a-b", "a" * 21, "é", "１２"],
)
def test_invalid_labels(label: str) -> None:
    with pytest.raises(InvalidLabelError) as caught:
        normalize_labels((label,))
    assert caught.value.code == "invalid_label"
    assert caught.value.details == {"label": label}


def test_labels_are_deduplicated_and_ascii_sorted() -> None:
    assert normalize_labels(("z", "a_", "a:1", "z")) == ("a:1", "a_", "z")


@pytest.mark.parametrize("value", [" project ", "x" * 100])
def test_project_name_is_trimmed_and_bounded(value: str) -> None:
    result = validate_project_name(value)
    assert result == value.strip()


@pytest.mark.parametrize("value", ["", "   ", "a\nb", "a\rb", "x" * 101])
def test_invalid_project_names(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_project_name(value)


def test_title_is_trimmed_and_body_line_endings_are_normalized() -> None:
    assert validate_title("  A title  ") == "A title"
    assert normalize_body("a\r\nb\rc\n") == "a\nb\nc\n"


@pytest.mark.parametrize("value", ["", "  ", "a\nb", "x" * 257])
def test_invalid_titles(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_title(value)


def make_ticket(**changes: object) -> Ticket:
    values: dict[str, object] = {
        "id": 1,
        "revision": 1,
        "title": "Title",
        "status": "open",
        "labels": (),
        "blocked_by": (),
        "created_at": NOW,
        "updated_at": NOW,
        "closed_at": None,
        "body": "body\n",
    }
    values.update(changes)
    return Ticket.model_validate(values)


def test_canonical_model_is_frozen_and_forbids_unknown_fields() -> None:
    item = make_ticket()
    with pytest.raises(ValidationError):
        Ticket.model_validate({**item.model_dump(), "unknown": True})
    with pytest.raises(ValidationError):
        item.title = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": 0},
        {"id": True},
        {"labels": ("z", "a")},
        {"labels": ("a", "a")},
        {"blocked_by": (2, 1)},
        {"blocked_by": (1, 1)},
        {"body": "bad\rline"},
        {"updated_at": NOW - timedelta(seconds=1)},
        {"status": "open", "closed_at": NOW},
        {"status": "completed", "closed_at": None},
    ],
)
def test_canonical_resource_invariants(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_ticket(**changes)


def test_canonical_timestamp_accepts_only_utc_seconds() -> None:
    assert make_ticket(created_at="2026-08-05T18:59:36Z").created_at == NOW
    for invalid in (
        "2026-08-05T18:59:36+00:00",
        "2026-08-05T18:59:36.1Z",
        NOW.replace(microsecond=1),
        NOW.astimezone(timezone(timedelta(hours=1))),
        datetime(2026, 8, 5, 18, 59, 36),
    ):
        with pytest.raises(ValidationError):
            make_ticket(created_at=invalid)


def test_structured_task_identity_is_preserved_by_domain_model() -> None:
    task = Task(
        identity=TaskIdentity(ticket_id=12, number=10),
        revision=1,
        title="Task",
        status=ResourceStatus.OPEN,
        created_at=NOW,
        updated_at=NOW,
        closed_at=None,
    )
    assert task.public_id == "12.10"
    assert task.ticket_id == 12
    assert task.number == 10


def test_project_canonical_name_is_not_silently_trimmed() -> None:
    with pytest.raises(ValidationError):
        Project(name=" Project ", root="/opaque", created_at=NOW)
