#!/usr/bin/env python3
"""Emit token-efficient, read-only context from the Wyrd CLI."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

TICKET_FIELDS = (
    "active_blocked_by",
    "active_blocking",
    "id",
    "is_blocked",
    "labels",
    "revision",
    "status",
    "tasks_summary",
    "title",
)
TASK_FIELDS = (
    "active",
    "active_blocked_by",
    "active_blocking",
    "id",
    "is_blocked",
    "labels",
    "revision",
    "status",
    "title",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Return compact, read-only Wyrd ticket or task context."
    )
    parser.add_argument(
        "--wyrd",
        default="wyrd",
        metavar="PATH",
        help="wyrd executable to invoke (default: wyrd from PATH)",
    )
    resources = parser.add_subparsers(dest="resource", required=True)

    tickets = resources.add_parser("tickets", help="List compact ticket context")
    tickets.add_argument("--status", default="open")
    tickets.add_argument("--label", action="append", default=[])
    tickets.add_argument("--text")
    tickets.add_argument("--lock-timeout")

    tasks = resources.add_parser("tasks", help="List compact task context")
    tasks.add_argument("--ticket", required=True)
    tasks.add_argument("--status", default="open")
    tasks.add_argument("--lock-timeout")
    return parser


def _command(arguments: argparse.Namespace) -> list[str]:
    resource = "ticket" if arguments.resource == "tickets" else "task"
    command = [arguments.wyrd, resource, "list"]
    if arguments.resource == "tickets":
        command.extend(("--status", arguments.status))
        for label in arguments.label:
            command.extend(("--label", label))
        if arguments.text is not None:
            command.extend(("--text", arguments.text))
    else:
        command.extend(("--ticket", arguments.ticket, "--status", arguments.status))
    if arguments.lock_timeout is not None:
        command.extend(("--lock-timeout", arguments.lock_timeout))
    command.append("--json")
    return command


def _error(code: str, message: str, details: dict[str, Any]) -> None:
    value = {"error": {"code": code, "details": details, "message": message}}
    sys.stderr.write(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def _project(value: object, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("Wyrd list output is not an array of objects.")
    missing = sorted(
        {
            field
            for item in value
            for field in fields
            if field not in item
        }
    )
    if missing:
        raise ValueError(f"Wyrd list output is missing fields: {', '.join(missing)}.")
    return [{field: item[field] for field in fields} for item in value]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    command = _command(arguments)
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
        )
    except FileNotFoundError:
        _error(
            "wyrd_not_found",
            "The wyrd executable was not found.",
            {"executable": arguments.wyrd},
        )
        return 127
    except (OSError, UnicodeError) as error:
        _error(
            "context_error",
            "The compact context helper could not execute wyrd.",
            {"reason": str(error)},
        )
        return 1

    if completed.returncode != 0:
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        return completed.returncode

    try:
        decoded = json.loads(completed.stdout)
        fields = TICKET_FIELDS if arguments.resource == "tickets" else TASK_FIELDS
        compact = _project(decoded, fields)
    except (json.JSONDecodeError, ValueError) as error:
        _error(
            "invalid_wyrd_output",
            "The wyrd executable returned an unexpected JSON result.",
            {"reason": str(error)},
        )
        return 1

    sys.stdout.write(
        json.dumps(
            compact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
