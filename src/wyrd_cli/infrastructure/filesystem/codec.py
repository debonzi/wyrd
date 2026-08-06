"""Strict safe YAML and exact Markdown-front-matter codecs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode

from wyrd_cli.domain.errors import UnsupportedSchemaError
from wyrd_cli.domain.models import Project, Task, TaskIdentity, Ticket
from wyrd_cli.domain.values import LABEL_PATTERN

_PROJECT_FIELDS = ("schema_version", "name", "created_at")
_RESOURCE_FIELDS = (
    "id",
    "revision",
    "title",
    "status",
    "labels",
    "blocked_by",
    "created_at",
    "updated_at",
    "closed_at",
)
_STATUSES = {"open", "completed", "dismissed"}


@dataclass(frozen=True)
class CodecFailure(Exception):
    code: str
    message: str
    details: dict[str, Any]

    def __str__(self) -> str:
        return self.message


class DuplicateKeyError(yaml.YAMLError):
    def __init__(self, key: object) -> None:
        super().__init__(f"duplicate mapping key: {key!r}")
        self.key = key


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _StrictSafeLoader, node: MappingNode, deep: bool = False
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise DuplicateKeyError(key)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


class _QuotedString(str):
    pass


class _CanonicalDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: object) -> bool:
        return True

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _represent_quoted_string(
    dumper: _CanonicalDumper, value: _QuotedString
) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style='"')


_CanonicalDumper.add_representer(_QuotedString, _represent_quoted_string)


def format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def encode_project(project: Project) -> bytes:
    payload = {
        "schema_version": project.schema_version,
        "name": project.name,
        "created_at": _QuotedString(format_timestamp(project.created_at)),
    }
    return _dump(payload).encode("utf-8")


def decode_project(data: bytes, *, root: str) -> Project:
    text = _decode_utf8(data)
    if "\r" in text:
        raise CodecFailure(
            "invalid_line_endings",
            "Persisted text must use LF line endings.",
            {},
        )
    raw = _load_mapping(text)
    _check_fields(raw, _PROJECT_FIELDS)
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise CodecFailure(
            "invalid_schema_version",
            "schema_version must be the integer 1.",
            {"schema_version": version},
        )
    if version != 1:
        raise UnsupportedSchemaError(
            f"Persistence schema version {version} is unsupported.",
            {"schema_version": version, "supported_schema_version": 1},
        )
    if not isinstance(raw["name"], str):
        raise CodecFailure("invalid_project_name", "Project name must be text.", {})
    if not isinstance(raw["created_at"], str):
        raise CodecFailure(
            "invalid_timestamp", "created_at must be a quoted timestamp string.", {}
        )
    try:
        return Project(
            schema_version=version,
            name=raw["name"],
            created_at=raw["created_at"],
            root=root,
        )
    except ValidationError as error:
        raise _model_failure(error) from error


def encode_ticket(ticket: Ticket) -> bytes:
    payload = _resource_payload(
        resource_id=ticket.id,
        revision=ticket.revision,
        title=ticket.title,
        status=ticket.status.value,
        labels=ticket.labels,
        blocked_by=ticket.blocked_by,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
    )
    return _encode_front_matter(payload, ticket.body)


def decode_ticket(data: bytes, *, expected_id: int) -> Ticket:
    raw, body = _decode_front_matter(data)
    _validate_resource_mapping(raw, expected_id=expected_id)
    try:
        return Ticket(
            id=raw["id"],
            revision=raw["revision"],
            title=raw["title"],
            status=raw["status"],
            labels=tuple(raw["labels"]),
            blocked_by=tuple(raw["blocked_by"]),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            closed_at=raw["closed_at"],
            body=body,
        )
    except ValidationError as error:
        raise _model_failure(error) from error


def encode_task(task: Task) -> bytes:
    payload = _resource_payload(
        resource_id=task.number,
        revision=task.revision,
        title=task.title,
        status=task.status.value,
        labels=task.labels,
        blocked_by=task.blocked_by,
        created_at=task.created_at,
        updated_at=task.updated_at,
        closed_at=task.closed_at,
    )
    return _encode_front_matter(payload, task.body)


def decode_task(data: bytes, *, ticket_id: int, expected_number: int) -> Task:
    raw, body = _decode_front_matter(data)
    _validate_resource_mapping(raw, expected_id=expected_number)
    try:
        return Task(
            identity=TaskIdentity(ticket_id=ticket_id, number=raw["id"]),
            revision=raw["revision"],
            title=raw["title"],
            status=raw["status"],
            labels=tuple(raw["labels"]),
            blocked_by=tuple(raw["blocked_by"]),
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            closed_at=raw["closed_at"],
            body=body,
        )
    except ValidationError as error:
        raise _model_failure(error) from error


def _resource_payload(
    *,
    resource_id: int,
    revision: int,
    title: str,
    status: str,
    labels: tuple[str, ...],
    blocked_by: tuple[int, ...],
    created_at: datetime,
    updated_at: datetime,
    closed_at: datetime | None,
) -> dict[str, object]:
    return {
        "id": resource_id,
        "revision": revision,
        "title": title,
        "status": status,
        "labels": list(labels),
        "blocked_by": list(blocked_by),
        "created_at": _QuotedString(format_timestamp(created_at)),
        "updated_at": _QuotedString(format_timestamp(updated_at)),
        "closed_at": (
            None if closed_at is None else _QuotedString(format_timestamp(closed_at))
        ),
    }


def _encode_front_matter(payload: dict[str, object], body: str) -> bytes:
    if "\r" in body:
        raise ValueError("canonical body must use LF line endings")
    return ("---\n" + _dump(payload) + "---\n" + body).encode("utf-8")


def _decode_front_matter(data: bytes) -> tuple[dict[str, Any], str]:
    text = _decode_utf8(data)
    if "\r" in text:
        raise CodecFailure(
            "invalid_line_endings",
            "Persisted text must use LF line endings.",
            {},
        )
    lines = text.splitlines(keepends=True)
    if not lines or lines[0] != "---\n":
        raise CodecFailure(
            "invalid_front_matter",
            "Resource must begin with an exact front-matter delimiter.",
            {},
        )
    closing = next(
        (index for index in range(1, len(lines)) if lines[index] == "---\n"),
        None,
    )
    if closing is None:
        raise CodecFailure(
            "invalid_front_matter",
            "Resource has no exact closing front-matter delimiter.",
            {},
        )
    raw = _load_mapping("".join(lines[1:closing]))
    body = "".join(lines[closing + 1 :])
    return raw, body


def _decode_utf8(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CodecFailure(
            "invalid_utf8",
            "Canonical text is not valid UTF-8.",
            {"offset": error.start},
        ) from error


def _load_mapping(text: str) -> dict[str, Any]:
    try:
        raw = yaml.load(text, Loader=_StrictSafeLoader)
    except DuplicateKeyError as error:
        raise CodecFailure(
            "duplicate_key",
            f"YAML mapping key {error.key!r} occurs more than once.",
            {"key": str(error.key)},
        ) from error
    except yaml.YAMLError as error:
        raise CodecFailure("invalid_yaml", "YAML is invalid.", {}) from error
    if not isinstance(raw, dict):
        raise CodecFailure("invalid_yaml", "YAML document must be a mapping.", {})
    if any(not isinstance(key, str) for key in raw):
        raise CodecFailure("invalid_yaml", "YAML mapping keys must be strings.", {})
    return raw


def _check_fields(raw: dict[str, Any], expected: tuple[str, ...]) -> None:
    expected_set = set(expected)
    unknown = sorted(set(raw) - expected_set)
    missing = sorted(expected_set - set(raw))
    if unknown:
        raise CodecFailure(
            "unknown_field",
            f"Unknown persisted field: {unknown[0]}.",
            {"fields": unknown},
        )
    if missing:
        raise CodecFailure(
            "missing_field",
            f"Required persisted field is missing: {missing[0]}.",
            {"fields": missing},
        )


def _validate_resource_mapping(raw: dict[str, Any], *, expected_id: int) -> None:
    _check_fields(raw, _RESOURCE_FIELDS)
    resource_id = raw["id"]
    if isinstance(resource_id, bool) or not isinstance(resource_id, int) or resource_id < 1:
        raise CodecFailure("invalid_id", "Resource ID must be a positive integer.", {})
    if resource_id != expected_id:
        raise CodecFailure(
            "id_path_mismatch",
            f"Resource ID {resource_id} does not match path identity {expected_id}.",
            {"id": resource_id, "path_id": expected_id},
        )
    revision = raw["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise CodecFailure(
            "invalid_revision", "Revision must be a positive integer.", {}
        )
    if not isinstance(raw["title"], str):
        raise CodecFailure("invalid_title", "Title must be text.", {})
    if not isinstance(raw["status"], str) or raw["status"] not in _STATUSES:
        raise CodecFailure("invalid_status", "Persisted status is invalid.", {})
    _validate_string_list(raw["labels"], name="labels", positive=False)
    for label in raw["labels"]:
        if LABEL_PATTERN.fullmatch(label) is None:
            raise CodecFailure(
                "invalid_label", f"Label '{label}' is invalid.", {"label": label}
            )
    _validate_integer_list(raw["blocked_by"], name="blocked_by")
    for field in ("created_at", "updated_at"):
        if not isinstance(raw[field], str):
            raise CodecFailure(
                "invalid_timestamp", f"{field} must be a quoted timestamp string.", {"field": field}
            )
    if raw["closed_at"] is not None and not isinstance(raw["closed_at"], str):
        raise CodecFailure(
            "invalid_timestamp",
            "closed_at must be null or a quoted timestamp string.",
            {"field": "closed_at"},
        )


def _validate_string_list(value: object, *, name: str, positive: bool) -> None:
    del positive
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CodecFailure("invalid_list", f"{name} must be a list of strings.", {"field": name})
    _validate_ordered_unique(value, name=name)


def _validate_integer_list(value: object, *, name: str) -> None:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value
    ):
        raise CodecFailure(
            "invalid_list", f"{name} must be a list of positive integers.", {"field": name}
        )
    _validate_ordered_unique(value, name=name)


def _validate_ordered_unique(value: list[Any], *, name: str) -> None:
    if len(set(value)) != len(value):
        raise CodecFailure(
            "duplicate_list_item",
            f"{name} contains a duplicate item.",
            {"field": name},
        )
    if value != sorted(value):
        raise CodecFailure(
            "unsorted_list", f"{name} must be sorted.", {"field": name}
        )


def _model_failure(error: ValidationError) -> CodecFailure:
    first = error.errors(include_url=False)[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = first.get("msg", "Persisted model is invalid.")
    code = "invalid_timestamp" if "_at" in location else "invalid_persisted_model"
    if location == "title":
        code = "invalid_title"
    elif location in {"status", "closed_at"} or not location:
        code = "invalid_lifecycle"
    return CodecFailure(
        code,
        f"Persisted model is invalid at {location or 'resource'}: {message}",
        {"field": location} if location else {},
    )


def _dump(payload: dict[str, object]) -> str:
    return yaml.dump(
        payload,
        Dumper=_CanonicalDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10_000,
        line_break="\n",
    )
