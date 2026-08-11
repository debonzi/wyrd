"""Human and deterministic JSON renderers for application DTOs."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TextIO

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from wyrd_cli.application.dto import (
    DependencyRelationDTO,
    DependencyViewDTO,
    DoctorReportDTO,
    LabelUsageDTO,
    ProjectDTO,
    ProjectStatusDTO,
    TaskDTO,
    TaskSummaryDTO,
    TicketDTO,
    TicketSummaryDTO,
    TicketTreeBranchDTO,
)

from .serialization import dumps


def styling_enabled(
    stream: TextIO,
    *,
    no_color: bool,
    json_output: bool = False,
    tty: bool | None = None,
) -> bool:
    return (
        not json_output
        and not no_color
        and "NO_COLOR" not in os.environ
        and (bool(stream.isatty()) if tty is None else tty)
    )


def emit_json(value: object) -> None:
    typer.echo(dumps(value))


def render_project(project: ProjectDTO, *, stream: TextIO, styled: bool) -> None:
    _write_lines(
        stream,
        (
            f"Project: {project.name}",
            f"Root: {project.root}",
            f"Schema version: {project.schema_version}",
            f"Created: {_timestamp(project.created_at)}",
        ),
        styled=styled,
    )


def render_status(status: ProjectStatusDTO, *, stream: TextIO, styled: bool) -> None:
    lines = (
        f"Project: {status.project.name}",
        f"Root: {status.project.root}",
        f"Schema version: {status.project.schema_version}",
        f"Created: {_timestamp(status.project.created_at)}",
        "Tickets:",
        f"  total: {status.tickets.total}",
        f"  open: {status.tickets.open}",
        f"  completed: {status.tickets.completed}",
        f"  dismissed: {status.tickets.dismissed}",
        f"  blocked: {status.tickets.blocked}",
        "Tasks:",
        f"  total: {status.tasks.total}",
        f"  open: {status.tasks.open}",
        f"  completed: {status.tasks.completed}",
        f"  dismissed: {status.tasks.dismissed}",
        f"  active open: {status.tasks.active_open}",
        f"  inactive open: {status.tasks.inactive_open}",
        f"  blocked: {status.tasks.blocked}",
        f"Distinct labels: {status.labels.distinct}",
    )
    _write_lines(stream, lines, styled=styled)


def render_ticket_list(
    tickets: Iterable[TicketDTO], *, stream: TextIO, styled: bool
) -> None:
    headers = ("ID", "status", "title", "labels", "blocked", "updated time")
    rows = [
        (
            str(ticket.id),
            ticket.status.value,
            ticket.title,
            ",".join(ticket.labels),
            "yes" if ticket.is_blocked else "no",
            _timestamp(ticket.updated_at),
        )
        for ticket in tickets
    ]
    _table(stream, headers, rows, styled=styled)


def render_task_list(tasks: Iterable[TaskDTO], *, stream: TextIO, styled: bool) -> None:
    headers = ("ID", "status", "active", "title", "labels", "blocked", "updated time")
    rows = [
        (
            task.id,
            task.status.value,
            "yes" if task.active else "no",
            task.title,
            ",".join(task.labels),
            "yes" if task.is_blocked else "no",
            _timestamp(task.updated_at),
        )
        for task in tasks
    ]
    _table(stream, headers, rows, styled=styled)


def render_tree(
    branches: Iterable[TicketTreeBranchDTO], *, stream: TextIO, styled: bool
) -> None:
    values = tuple(branches)
    if not values:
        _write_lines(stream, ("No matching tickets.",), styled=styled)
        return

    lines: list[str] = []
    for branch in values:
        lines.append(_tree_resource_line(branch.ticket))
        if branch.tasks:
            for index, task in enumerate(branch.tasks):
                connector = "└──" if index == len(branch.tasks) - 1 else "├──"
                lines.append(f"  {connector} {_tree_resource_line(task)}")
        else:
            description = (
                "no tasks"
                if branch.ticket.tasks_summary.total == 0
                else "no matching tasks"
            )
            lines.append(f"  └── {description}")
    _write_lines(stream, lines, styled=styled)


def render_resource(
    resource: TicketDTO | TaskDTO, *, stream: TextIO, styled: bool
) -> None:
    if isinstance(resource, TicketDTO):
        lines = (
            "Type: ticket",
            f"ID: {resource.id}",
            f"Status: {resource.status.value}",
            f"Active: {_yes_no(resource.active)}",
            f"Revision: {resource.revision}",
            f"Title: {resource.title}",
            f"Labels: {','.join(resource.labels)}",
            f"Blocked by: {_joined(resource.blocked_by)}",
            f"Blocking: {_joined(resource.blocking)}",
            f"Active blocked by: {_joined(resource.active_blocked_by)}",
            f"Active blocking: {_joined(resource.active_blocking)}",
            f"Blocked: {_yes_no(resource.is_blocked)}",
            f"Tasks: {_joined(resource.tasks)}",
            (
                "Tasks summary: "
                f"total={resource.tasks_summary.total}, open={resource.tasks_summary.open}, "
                f"completed={resource.tasks_summary.completed}, dismissed={resource.tasks_summary.dismissed}, "
                f"active_open={resource.tasks_summary.active_open}, "
                f"inactive_open={resource.tasks_summary.inactive_open}"
            ),
            f"Created: {_timestamp(resource.created_at)}",
            f"Updated: {_timestamp(resource.updated_at)}",
            f"Closed: {_timestamp(resource.closed_at)}",
            "Body:",
        )
    else:
        lines = (
            "Type: task",
            f"ID: {resource.id}",
            f"Ticket ID: {resource.ticket_id}",
            f"Number: {resource.number}",
            f"Status: {resource.status.value}",
            f"Active: {_yes_no(resource.active)}",
            f"Revision: {resource.revision}",
            f"Title: {resource.title}",
            f"Labels: {','.join(resource.labels)}",
            f"Blocked by: {_joined(resource.blocked_by)}",
            f"Blocking: {_joined(resource.blocking)}",
            f"Active blocked by: {_joined(resource.active_blocked_by)}",
            f"Active blocking: {_joined(resource.active_blocking)}",
            f"Blocked: {_yes_no(resource.is_blocked)}",
            f"Created: {_timestamp(resource.created_at)}",
            f"Updated: {_timestamp(resource.updated_at)}",
            f"Closed: {_timestamp(resource.closed_at)}",
            "Body:",
        )
    _write_lines(stream, lines, styled=styled)
    if styled:
        if resource.body:
            _console(stream, styled=True).print(Markdown(resource.body))
    else:
        # Metadata already ends with ``Body:\n``; bytes after it mirror the DTO body.
        stream.write(resource.body)
        stream.flush()


def render_labels(
    labels: Iterable[LabelUsageDTO], *, stream: TextIO, styled: bool
) -> None:
    _table(
        stream,
        ("name", "ticket count", "task count", "total count"),
        [
            (
                item.name,
                str(item.ticket_count),
                str(item.task_count),
                str(item.total_count),
            )
            for item in labels
        ],
        styled=styled,
    )


def render_doctor(report: DoctorReportDTO, *, stream: TextIO, styled: bool) -> None:
    if report.healthy:
        _write_lines(stream, ("Project is healthy.",), styled=styled)
        return
    _table(
        stream,
        ("path", "code", "message"),
        [(problem.path, problem.code, problem.message) for problem in report.problems],
        styled=styled,
    )


def render_dependencies(
    result: DependencyViewDTO, *, stream: TextIO, styled: bool
) -> None:
    resource = result.resource
    _write_lines(
        stream,
        (
            f"Resource: {resource.id}",
            f"Status: {resource.status.value}",
            "Blocked by:",
        ),
        styled=styled,
    )
    _relationship_table(result.blocked_by, stream=stream, styled=styled)
    _write_lines(stream, ("Blocking:",), styled=styled)
    _relationship_table(result.blocking, stream=stream, styled=styled)


def _relationship_table(
    relations: Iterable[DependencyRelationDTO], *, stream: TextIO, styled: bool
) -> None:
    _table(
        stream,
        ("ID", "status", "effective"),
        [
            (
                str(relation.id),
                relation.status.value,
                _yes_no(relation.effective),
            )
            for relation in relations
        ],
        styled=styled,
    )


def _table(
    stream: TextIO,
    headers: tuple[str, ...],
    rows: Iterable[tuple[str, ...]],
    *,
    styled: bool,
) -> None:
    if styled:
        table = Table(show_header=True, header_style="bold")
        for header in headers:
            table.add_column(header)
        for row in rows:
            table.add_row(*row)
        _console(stream, styled=True).print(table)
        return
    stream.write(" | ".join(headers) + "\n")
    for row in rows:
        stream.write(" | ".join(row) + "\n")
    stream.flush()


def _write_lines(stream: TextIO, lines: Iterable[str], *, styled: bool) -> None:
    if styled:
        console = _console(stream, styled=True)
        for line in lines:
            console.print(line)
        return
    for line in lines:
        stream.write(line + "\n")
    stream.flush()


def _console(stream: TextIO, *, styled: bool) -> Console:
    return Console(
        file=stream,
        force_terminal=styled,
        no_color=not styled,
        color_system="auto" if styled else None,
        highlight=False,
        markup=False,
    )


def _tree_resource_line(resource: TicketSummaryDTO | TaskSummaryDTO) -> str:
    state = [resource.status.value]
    if (
        isinstance(resource, TaskSummaryDTO)
        and resource.status.value == "open"
        and not resource.active
    ):
        state.append("inactive")
    if resource.is_blocked:
        state.append(f"blocked by: {_joined(resource.active_blocked_by)}")
    labels = f" [labels: {','.join(resource.labels)}]" if resource.labels else ""
    return f"{resource.id} [{'; '.join(state)}] {resource.title}{labels}"


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _joined(values: Iterable[object]) -> str:
    return ",".join(str(value) for value in values)
